from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import json
import subprocess
import requests
import sqlite3
import uuid
from datetime import datetime
import psutil
import threading
import time

cached_public_ip = "Yükleniyor..."
def fetch_public_ip():
    global cached_public_ip
    while True:
        try:
            r = requests.get('https://api.ipify.org?format=json', timeout=5)
            cached_public_ip = r.json().get('ip', 'Hata')
        except:
            pass
        time.sleep(3600)

threading.Thread(target=fetch_public_ip, daemon=True).start()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'pi-node-fleet-secret-key-2026')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

PASSWORD = '435102'
BASE_DIR = '/opt/pi-fleet'
NODES_DIR = os.path.join(BASE_DIR, 'nodes')
DB_PATH = os.path.join(BASE_DIR, 'fleet.db')

os.makedirs(NODES_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS nodes 
                 (id TEXT PRIMARY KEY, name TEXT, port_prefix TEXT, created_at TEXT, auto_restart INTEGER, downtime_minutes INTEGER, bind_ip TEXT, base_port INTEGER)''')
    conn.commit()
    conn.close()

init_db()


def get_capsule_name(port_prefix, bind_ip=None, base_port=None):
    try:
        import subprocess
        import re
        out = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}\t{{.Ports}}'], text=True)
        for line in out.splitlines():
            if 'pinode_capsule_' in line:
                if bind_ip and base_port:
                    if re.search(rf'{bind_ip}:{base_port}[->]', line):
                        return line.split('\t')[0]
                elif port_prefix:
                    if re.search(rf':{port_prefix}1[->]', line):
                        return line.split('\t')[0]
    except Exception:
        pass
    return None

def is_authenticated():
    return session.get('logged_in', False)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if check_password(request.form.get('password', '')):
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Hatalı şifre')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    if not is_authenticated():
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/api/system')
def system_stats():
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    temp = "N/A"
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = str(round(int(f.read().strip()) / 1000, 1)) + " °C"
    except:
        pass

    disk_io = "N/A"
    try:
        out = subprocess.check_output("iostat -dx 1 2 | awk '/^dm-|^sd/ {print $NF}' | tail -n 2 | sort -nr | head -1", shell=True, text=True).strip()
        disk_io = f"%{out}"
    except:
        pass

    return jsonify({
        'cpu': f"{cpu}%",
        'ram_used': f"{ram.used // (1024**3)} GB",
        'ram_total': f"{ram.total // (1024**3)} GB",
        'ram_percent': f"{ram.percent}%",
        'disk_used': f"{disk.used // (1024**3)} GB",
        'disk_total': f"{disk.total // (1024**3)} GB",
        'disk_percent': f"{disk.percent}%",
        'disk_io': disk_io,
        'temp': temp,
        'public_ip': cached_public_ip
    })


@app.route('/api/nodes', methods=['GET'])
def get_nodes():
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, name, port_prefix, created_at, auto_restart, downtime_minutes, bind_ip, base_port FROM nodes')
    nodes = []
    for row in c.fetchall():
        node_id = row[0]
        name = row[1]
        port_prefix = row[2]
        created_at_str = row[3] if len(row) > 3 else None
        auto_restart = row[4] if len(row) > 4 else 0
        downtime_minutes = row[5] if len(row) > 5 else 0
        bind_ip = row[6] if len(row) > 6 else None
        base_port = row[7] if len(row) > 7 else None
        
        availability = "100.00%"
        try:
            from datetime import datetime
            if created_at_str:
                created_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                total_minutes = max(1.0, (datetime.utcnow() - created_dt).total_seconds() / 60.0)
                avail_pct = 100.0 * max(0, (total_minutes - downtime_minutes)) / total_minutes
                availability = f"{avail_pct:.2f}%"
        except Exception as e:
            pass
        
        capsule_name = get_capsule_name(port_prefix, bind_ip, base_port)
        if not capsule_name:
            nodes.append({
                'id': node_id, 'name': name, 'port_prefix': port_prefix, 'bind_ip': bind_ip, 'base_port': base_port,
                'status': 'Durduruldu (Kapsül Yok)', 'uuid': '❌ Tanımsız',
                'sync_status': 'Erişilemiyor', 'state': '-', 'detail': '',
                'protocol': '-', 'incoming': '-', 'auto_restart': auto_restart
            })
            continue

        try:
            docker_out = subprocess.check_output(['docker', 'exec', capsule_name, 'docker', 'ps', '-f', 'name=mainnet', '--format', '{{.Status}}'], text=True, timeout=10).strip()
            status = docker_out if docker_out else "Durduruldu"
        except Exception:
            status = "Hata"
            
        uuid_val = "Tanımsız"
        try:
            out = subprocess.check_output(['docker', 'exec', capsule_name, 'cat', '/home/pi-node/user-preferences.json'], text=True, timeout=5)
            if out.strip().startswith('{'):
                try: 
                    import json as tmp_json
                    uuid_val = tmp_json.loads(out).get('uuid', 'Kayıtlı')
                except: 
                    uuid_val = "Şifreli (Kayıtlı)"
            elif out.strip():
                uuid_val = "Şifreli (Kayıtlı)"
        except:
            pass
            
        sync_status = "Bilinmiyor"
        state = "-"
        incoming = "-"
        protocol = "-"
        hz_ledger = 0
        detail = ""
        
        try:
            api_port = base_port if base_port else f"{port_prefix}1"
            target_ip = bind_ip if bind_ip else "localhost"
            r = requests.get(f'http://{target_ip}:{api_port}/', timeout=5)
            if r.status_code == 200:
                data = r.json()
                hz_ledger = data.get("core_latest_ledger", data.get("ingest_latest_ledger", 0))
                sync_status = f"Ledger: {hz_ledger}"
        except:
            sync_status = "Erişilemiyor"
            
        try:
            out = subprocess.check_output(['docker', 'exec', capsule_name, 'docker', 'exec', 'mainnet', 'curl', '-s', 'http://localhost:11626/info'], text=True, timeout=10)
            info_data = json.loads(out).get('info', {})
            state = info_data.get('state', '-')
            protocol = info_data.get('protocol_version', '-')
            incoming = info_data.get('peers', {}).get('authenticated_count', 0)
            
            status_array = info_data.get('status', [])
            detail = status_array[0] if status_array else ""
            
            core_ledger = info_data.get('ledger', {}).get('num', 0)
            if hz_ledger == 0 and core_ledger > 0:
                sync_status = f"Ledger: {core_ledger}"
        except Exception as e:
            pass

        nodes.append({
            'id': node_id, 'name': name, 'port_prefix': port_prefix, 'bind_ip': bind_ip, 'base_port': base_port,
            'status': status, 'uuid': uuid_val, 'sync_status': sync_status, 'availability': availability,
            'state': state, 'detail': detail, 'protocol': protocol,
            'incoming': incoming, 'auto_restart': auto_restart
        })
    conn.close()
    return jsonify(nodes)

@app.route('/api/nodes/create', methods=['POST'])
def create_node():
    return jsonify({'success': False, 'message': 'Kapsül mimarisinde arayüzden yeni node oluşturulamaz. Manuel kapsül kurulmalıdır.'})

@app.route('/api/nodes/<node_id>/delete', methods=['POST'])
def delete_node(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM nodes WHERE id = ?', (node_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Node DB den silindi (Kapsül verileri güvende).'})

@app.route('/api/nodes/<node_id>/action', methods=['POST'])
def node_action(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    action = request.json.get('action')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT port_prefix, bind_ip, base_port FROM nodes WHERE id = ?', (node_id,))
    row = c.fetchone()
    conn.close()
    if not row: return jsonify({'success': False, 'message': 'Node bulunamadı.'})
    
    port_prefix = row[0]
    bind_ip = row[1] if len(row) > 1 else None
    base_port = row[2] if len(row) > 2 else None
    capsule_name = get_capsule_name(port_prefix, bind_ip, base_port)
    if not capsule_name: return jsonify({'success': False, 'message': 'Bu node için çalışan bir Kapsül bulunamadı!'})
    
    try:
        if action == 'start':
            res = subprocess.run(['docker', 'exec', capsule_name, 'docker', 'start', 'mainnet'], capture_output=True, text=True)
            if res.returncode != 0: return jsonify({'success': False, 'message': f'Docker başlatılamadı: {res.stderr}'})
        elif action == 'stop':
            subprocess.run(['docker', 'exec', capsule_name, 'docker', 'stop', 'mainnet'])
        elif action == 'restart':
            res = subprocess.run(['docker', 'exec', capsule_name, 'docker', 'restart', 'mainnet'], capture_output=True, text=True)
            if res.returncode != 0: return jsonify({'success': False, 'message': f'Yeniden başlatılamadı: {res.stderr}'})
        elif action == 'wipe_db':
            subprocess.run(['docker', 'exec', capsule_name, 'docker', 'stop', 'mainnet'])
            subprocess.run(['docker', 'exec', capsule_name, 'bash', '-c', 'rm -rf /opt/stellar/core/* /history/*'])
            subprocess.run(['docker', 'exec', capsule_name, 'docker', 'start', 'mainnet'])
        else:
            return jsonify({'success': False, 'message': 'Geçersiz işlem.'})
            
        return jsonify({'success': True, 'message': 'İşlem başarılı.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Hata: {str(e)}'})

@app.route('/api/nodes/<node_id>/uuid', methods=['GET'])
def get_uuid(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT port_prefix, bind_ip, base_port FROM nodes WHERE id = ?', (node_id,))
    row = c.fetchone()
    conn.close()
    if not row: return jsonify({'success': False, 'message': 'Node bulunamadı.'})
    
    port_prefix = row[0]
    bind_ip = row[1] if len(row) > 1 else None
    base_port = row[2] if len(row) > 2 else None
    capsule_name = get_capsule_name(port_prefix, bind_ip, base_port)
    if not capsule_name: return jsonify({'success': False, 'message': 'Kapsül yok.'})

    content = ""
    try:
        out = subprocess.check_output(['docker', 'exec', capsule_name, 'cat', '/home/pi-node/user-preferences.json'], text=True, timeout=5)
        content = out
    except:
        pass
    return jsonify({'success': True, 'content': content})

@app.route('/api/nodes/<node_id>/uuid', methods=['POST'])
def update_uuid(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    content_payload = request.json.get('content', '').strip()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT port_prefix, bind_ip, base_port FROM nodes WHERE id = ?', (node_id,))
    row = c.fetchone()
    conn.close()
    if not row: return jsonify({'success': False, 'message': 'Node bulunamadı.'})
    
    port_prefix = row[0]
    bind_ip = row[1] if len(row) > 1 else None
    base_port = row[2] if len(row) > 2 else None
    capsule_name = get_capsule_name(port_prefix, bind_ip, base_port)
    if not capsule_name: return jsonify({'success': False, 'message': 'Kapsül yok.'})
        
    try:
        try:
            parsed = json.loads(content_payload)
            final_content = json.dumps(parsed, indent=2)
        except:
            final_content = content_payload
            
        import tempfile
        import os
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w') as f:
            f.write(final_content)
            
        subprocess.run(['docker', 'cp', path, f"{capsule_name}:/home/pi-node/user-preferences.json"])
        os.remove(path)
        
        return jsonify({'success': True, 'message': 'UUID başarıyla kaydedildi.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/nodes/<node_id>/set_seed', methods=['POST'])
def set_seed(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    seed = request.json.get('seed', '').strip()
    if not seed: return jsonify({'success': False, 'message': 'Seed boş olamaz.'})
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT port_prefix, bind_ip, base_port FROM nodes WHERE id = ?', (node_id,))
    row = c.fetchone()
    conn.close()
    if not row: return jsonify({'success': False, 'message': 'Node bulunamadı.'})
    
    port_prefix = row[0]
    bind_ip = row[1] if len(row) > 1 else None
    base_port = row[2] if len(row) > 2 else None
    capsule_name = get_capsule_name(port_prefix, bind_ip, base_port)
    if not capsule_name: return jsonify({'success': False, 'message': 'Kapsül yok.'})
        
    try:
        out = subprocess.check_output(['docker', 'exec', capsule_name, 'cat', '/home/pi-node/.env'], text=True)
        env_content = out
        import re
        if re.search(r'NODE_PRIVATE_KEY=.*', env_content):
            env_content = re.sub(r'NODE_PRIVATE_KEY=.*', f'NODE_PRIVATE_KEY={seed}', env_content)
        else:
            env_content += f'\\nNODE_PRIVATE_KEY={seed}\\n'
            
        import tempfile
        import os
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w') as f:
            f.write(env_content)
            
        subprocess.run(['docker', 'cp', path, f"{capsule_name}:/home/pi-node/.env"])
        os.remove(path)
        
        subprocess.run(['docker', 'exec', capsule_name, 'bash', '-c', f'echo "{seed}" > /home/pi-node/user-preferences.json'])
                
        def bg_restart():
            subprocess.run(['docker', 'exec', capsule_name, 'docker', 'restart', 'mainnet'])
        import threading
        threading.Thread(target=bg_restart, daemon=True).start()
        
        return jsonify({'success': True, 'message': 'Seed başarıyla kaydedildi ve Node yeniden başlatılıyor.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/nodes/<node_id>/rename', methods=['POST'])
def rename_node(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    new_name = request.json.get('name', '').strip()
    if not new_name: return jsonify({'success': False, 'message': 'İsim boş olamaz.'})
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE nodes SET name = ? WHERE id = ?', (new_name, node_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'İsim güncellendi.'})

@app.route('/api/nodes/<node_id>/toggle-restart', methods=['POST'])
def toggle_restart(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT auto_restart FROM nodes WHERE id = ?', (node_id,))
    row = c.fetchone()
    if not row:
        return jsonify({'success': False, 'message': 'Node bulunamadı.'})
    current_val = row[0]
    new_val = 1 if current_val == 0 else 0
    c.execute('UPDATE nodes SET auto_restart = ? WHERE id = ?', (new_val, node_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Oto-Başlat güncellendi.', 'auto_restart': new_val})

def watchdog_loop():
    while True:
        time.sleep(3600)

threading.Thread(target=watchdog_loop, daemon=True).start()

latest_available_image = None
is_updating = False

def check_updates_loop():
    while True:
        time.sleep(3600)

threading.Thread(target=check_updates_loop, daemon=True).start()

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if request.method == 'POST':
        data = request.json
        c.execute("UPDATE settings SET value=? WHERE key='auto_update'", (data.get('auto_update', '0'),))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    
    c.execute("SELECT value FROM settings WHERE key='auto_update'")
    res = c.fetchone()
    auto_update = res[0] if res else '0'
    c.execute("SELECT value FROM settings WHERE key='target_image'")
    res2 = c.fetchone()
    current_image = res2[0] if res2 else ''
    conn.close()
    
    return jsonify({
        "auto_update": auto_update,
        "current_image": current_image,
        "update_available": None,
        "is_updating": False
    })

@app.route('/api/update_all', methods=['POST'])
def api_update_all():
    return jsonify({"success": False, "msg": "Kapsül mimarisinde toplu güncelleme desteklenmez."})

def check_password(pwd):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='admin_password'")
        res = c.fetchone()
        conn.close()
        if res and res[0]:
            return pwd == res[0]
    except Exception:
        pass
    return pwd == PASSWORD

@app.route('/api/change_password', methods=['POST'])
def api_change_password():
    if not session.get('logged_in'):
        return jsonify({"success": False, "msg": "Unauthorized"})
    new_pass = request.json.get('password')
    if new_pass:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password', ?)", (new_pass,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "msg": "Şifre başarıyla değiştirildi!"})
    return jsonify({"success": False, "msg": "Geçersiz şifre"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3140)
