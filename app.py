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
        time.sleep(3600) # her saat yenile

threading.Thread(target=fetch_public_ip, daemon=True).start()

app = Flask(__name__)
app.secret_key = 'pi-node-fleet-secret-key-2026'

PASSWORD = '435102'
BASE_DIR = '/opt/pi-fleet'
NODES_DIR = os.path.join(BASE_DIR, 'nodes')
DB_PATH = os.path.join(BASE_DIR, 'fleet.db')

os.makedirs(NODES_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS nodes 
                 (id TEXT PRIMARY KEY, name TEXT, port_prefix TEXT, created_at TEXT)''')
    conn.commit()
    conn.close()

init_db()

def is_authenticated():
    return session.get('logged_in', False)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == PASSWORD:
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
    c.execute('SELECT id, name, port_prefix, created_at, auto_restart FROM nodes')
    nodes = []
    for row in c.fetchall():
        node_id = row[0]
        port_prefix = row[2]
        auto_restart = row[4] if len(row) > 4 else 0
        
        container_name = f'pi-node-{node_id}'
        try:
            docker_out = subprocess.check_output(['docker', 'ps', '-f', f'name={container_name}', '--format', '{{.Status}}'], text=True).strip()
            status = docker_out if docker_out else "Durduruldu"
        except Exception:
            status = "Hata"
            
        uuid_val = ""
        uuid_path = os.path.join(NODES_DIR, node_id, 'user-preferences.json')
        if os.path.exists(uuid_path):
            with open(uuid_path, 'r') as f:
                content = f.read().strip()
                if content.startswith('{'):
                    try:
                        uuid_val = json.loads(content).get('uuid', 'Kayıtlı')
                    except:
                        uuid_val = "Şifreli (Kayıtlı)"
                elif content:
                    uuid_val = "Şifreli (Kayıtlı)"
                    
        sync_status = "Bilinmiyor"
        state = "-"
        incoming = "-"
        protocol = "-"
        
        hz_ledger = 0
        try:
            api_port = f"{port_prefix}1"
            r = requests.get(f'http://localhost:{api_port}/', timeout=1)
            if r.status_code == 200:
                data = r.json()
                hz_ledger = data.get("core_latest_ledger", data.get("ingest_latest_ledger", 0))
                sync_status = f"Ledger: {hz_ledger}"
        except:
            sync_status = "Erişilemiyor"
            
        try:
            out = subprocess.check_output(['docker', 'exec', container_name, 'curl', '-s', 'http://localhost:11626/info'], text=True, timeout=8)
            info_data = json.loads(out).get('info', {})
            state = info_data.get('state', '-')
            protocol = info_data.get('protocol_version', '-')
            incoming = info_data.get('peers', {}).get('authenticated_count', 0)
            
            # Fetch details
            status_array = info_data.get('status', [])
            detail = status_array[0] if status_array else ""
            
            # Fallback to core ledger if horizon is 0
            core_ledger = info_data.get('ledger', {}).get('num', 0)
            if hz_ledger == 0 and core_ledger > 0:
                sync_status = f"Ledger: {core_ledger}"
        except Exception as e:
            detail = ""

        nodes.append({
            'id': node_id,
            'name': row[1],
            'port_prefix': port_prefix,
            'status': status,
            'uuid': uuid_val,
            'sync_status': sync_status,
            'state': state,
            'detail': detail,
            'protocol': protocol,
            'incoming': incoming,
            'auto_restart': auto_restart
        })
    conn.close()
    return jsonify(nodes)


def find_synced_node(exclude_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, port_prefix FROM nodes')
    nodes = c.fetchall()
    conn.close()
    for row in nodes:
        nid = row[0]
        if nid == exclude_id: continue
        container_name = f'pi-node-{nid}'
        try:
            out = subprocess.check_output(['docker', 'exec', container_name, 'curl', '-s', 'http://localhost:11626/info'], text=True, timeout=3)
            info_data = json.loads(out).get('info', {})
            if info_data.get('state', '') == 'Synced!':
                return nid
        except:
            pass
    return None


clone_status = {}

def async_clone(target_id, source_id):
    global clone_status
    source_path = os.path.join(NODES_DIR, source_id)
    target_path = os.path.join(NODES_DIR, target_id)
    
    clone_status[target_id] = "Kaynak durduruluyor..."
    subprocess.run(['docker', 'compose', 'stop'], cwd=source_path)
    
    subprocess.run(f'mkdir -p {target_path}/stellar-core', shell=True)
    subprocess.run(f'mkdir -p {target_path}/history', shell=True)
    
    clone_status[target_id] = "%0"
    
    import re
    cmd = f'rsync -a --info=progress2 --no-i-r {source_path}/stellar-core/. {target_path}/stellar-core/'
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        if '%' in line:
            m = re.search(r'(\d+)%', line)
            if m:
                clone_status[target_id] = f"%{m.group(1)} (Core Kopyalanıyor)"
    proc.wait()
    
    cmd2 = f'rsync -a --info=progress2 --no-i-r {source_path}/history/. {target_path}/history/'
    proc2 = subprocess.Popen(cmd2, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc2.stdout:
        if '%' in line:
            m = re.search(r'(\d+)%', line)
            if m:
                clone_status[target_id] = f"%{m.group(1)} (History Kopyalanıyor)"
    proc2.wait()
    
    clone_status[target_id] = "Node'lar Başlatılıyor..."
    subprocess.run(['docker', 'compose', 'start'], cwd=source_path)
    subprocess.run(['docker', 'compose', 'up', '-d'], cwd=target_path)
    
    clone_status[target_id] = "Tamamlandı"

@app.route('/api/nodes/<node_id>/clone_status', methods=['GET'])
def get_clone_status(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'status': clone_status.get(node_id, '')})

@app.route('/api/nodes/create', methods=['POST'])
def create_node():
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    name = request.json.get('name', 'Yeni Node')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    node_id = str(uuid.uuid4())[:8]
    
    c.execute('SELECT port_prefix FROM nodes')
    existing_prefixes = [int(row[0]) for row in c.fetchall()]
    prefix = 3140
    while prefix in existing_prefixes:
        prefix += 1
    
    port_prefix = str(prefix)
    
    node_path = os.path.join(NODES_DIR, node_id)
    os.makedirs(node_path, exist_ok=True)
    
    pg_pass = uuid.uuid4().hex
    
    try:
        out = subprocess.check_output(['docker', 'exec', 'pi-node-mainnet', 'stellar-core', 'gen-seed'], text=True)
        secret_seed = ""
        for line in out.splitlines():
            if line.startswith("Secret seed:"):
                secret_seed = line.split(":")[1].strip()
        if not secret_seed: raise Exception("No seed")
    except:
        secret_seed = "SDK2AFMEW4SD5V5MCXFBYIENTKFGLZFDCB2JBVJK26J5LNSHJACMQVMI"
        
    env_content = f'''POSTGRES_PASSWORD={pg_pass}
DATABASE_URL=postgres://stellar:{pg_pass}@localhost:5432/horizon
NETWORK_PASSPHRASE="Pi Network"
NODE_PRIVATE_KEY={secret_seed}
'''
    with open(os.path.join(node_path, '.env'), 'w') as f:
        f.write(env_content)
    
    try:
        target_img = 'pinetwork/pi-node-docker:organization-mainnet-v1.1-p23.0.1'
    except:
        target_img = 'pinetwork/pi-node-docker:organization-mainnet-v1.1-p23.0.1'

    compose_content = f'''name: pi-fleet-{node_id}

services:
  mainnet:
    image: {target_img}
    container_name: pi-node-{node_id}
    env_file:
      - ./.env
    restart: unless-stopped
    ports:
      - "{port_prefix}1:8000"
      - "{port_prefix}2:31402"
      - "{port_prefix}3:1570"
    volumes:
      - ./stellar-core:/opt/stellar
      - ./supervisor:/var/log/supervisor
      - ./history:/history
    command: ["--mainnet", "--enable-auto-migrations"]
'''
    with open(os.path.join(node_path, 'docker-compose.yml'), 'w') as f:
        f.write(compose_content)
        
    c.execute('INSERT INTO nodes (id, name, port_prefix, created_at) VALUES (?, ?, ?, ?)',
              (node_id, name, port_prefix, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    try:
        synced_node = find_synced_node(exclude_id=node_id)
        if synced_node:
            threading.Thread(target=async_clone, args=(node_id, synced_node), daemon=True).start()
            return jsonify({'success': True, 'message': 'Klonlama başladı.', 'is_async': True, 'node_id': node_id})
        
        res = subprocess.run(['docker', 'compose', 'up', '-d'], cwd=node_path, capture_output=True, text=True)
        if res.returncode != 0:
            return jsonify({'success': False, 'message': f'Docker başlatılamadı: {res.stderr}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Docker başlatılamadı: {str(e)}'})
        
    return jsonify({'success': True, 'message': 'Node başarıyla oluşturuldu.'})

@app.route('/api/nodes/<node_id>/delete', methods=['POST'])
def delete_node(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id FROM nodes WHERE id = ?', (node_id,))
    if not c.fetchone():
        return jsonify({'success': False, 'message': 'Node bulunamadı.'})
        
    node_path = os.path.join(NODES_DIR, node_id)
    if os.path.exists(node_path):
        try:
            subprocess.check_call(['docker', 'compose', 'down'], cwd=node_path)
            subprocess.check_call(['rm', '-rf', node_path])
        except Exception as e:
            pass
            
    c.execute('DELETE FROM nodes WHERE id = ?', (node_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Node silindi.'})

@app.route('/api/nodes/<node_id>/action', methods=['POST'])
def node_action(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    action = request.json.get('action')
    
    node_path = os.path.join(NODES_DIR, node_id)
    try:
        if action in ['start', 'restart']:
            uuid_path = os.path.join(node_path, 'user-preferences.json')
            if not os.path.exists(uuid_path) or os.path.getsize(uuid_path) < 10:
                return jsonify({'success': False, 'message': 'Lütfen önce sarı UUID butonuna tıklayarak kimliğinizi ekleyin! Kimlik (UUID) olmadan Node başlatılamaz.'})
                
        if action == 'start':
            res = subprocess.run(['docker', 'compose', 'up', '-d'], cwd=node_path, capture_output=True, text=True)
            if res.returncode != 0:
                return jsonify({'success': False, 'message': f'Docker başlatılamadı: {res.stderr}'})
        elif action == 'stop':
            subprocess.run(['docker', 'compose', 'stop'], cwd=node_path)
        elif action == 'restart':
            res = subprocess.run(['docker', 'compose', 'restart'], cwd=node_path, capture_output=True, text=True)
            if res.returncode != 0:
                return jsonify({'success': False, 'message': f'Docker yeniden başlatılamadı: {res.stderr}'})
        elif action == 'wipe_db':
            subprocess.run(['docker', 'compose', 'down', '-v'], cwd=node_path)
            subprocess.run('rm -rf stellar-core/*', cwd=node_path, shell=True)
            subprocess.run('rm -rf history/*', cwd=node_path, shell=True)
            
            synced_node = find_synced_node(exclude_id=node_id)
            if synced_node:
                threading.Thread(target=async_clone, args=(node_id, synced_node), daemon=True).start()
                return jsonify({'success': True, 'message': 'Sıfırlama klonlaması başladı.', 'is_async': True, 'node_id': node_id})
                
            res = subprocess.run(['docker', 'compose', 'up', '-d'], cwd=node_path, capture_output=True, text=True)
            if res.returncode != 0:
                return jsonify({'success': False, 'message': f'Docker başlatılamadı: {res.stderr}'})
        else:
            return jsonify({'success': False, 'message': 'Geçersiz işlem.'})
            
        return jsonify({'success': True, 'message': 'İşlem başarılı.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Hata: {str(e)}'})

@app.route('/api/nodes/<node_id>/uuid', methods=['GET'])
def get_uuid(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    uuid_path = os.path.join(NODES_DIR, node_id, 'user-preferences.json')
    content = ""
    if os.path.exists(uuid_path):
        with open(uuid_path, 'r') as f:
            content = f.read()
    
    return jsonify({'success': True, 'content': content})

@app.route('/api/nodes/<node_id>/uuid', methods=['POST'])
def update_uuid(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    content = request.json.get('content', '').strip()
    
    node_path = os.path.join(NODES_DIR, node_id)
    if not os.path.exists(node_path):
        return jsonify({'success': False, 'message': 'Node bulunamadı.'})
        
    try:
        try:
            parsed = json.loads(content)
            final_content = json.dumps(parsed, indent=2)
            
            new_uuid = parsed.get("uuid")
            if new_uuid:
                for other_node in os.listdir(NODES_DIR):
                    if other_node == node_id: continue
                    other_pref = os.path.join(NODES_DIR, other_node, 'user-preferences.json')
                    if os.path.exists(other_pref):
                        try:
                            import json as temp_json
                            with open(other_pref, 'r') as pf:
                                other_data = temp_json.load(pf)
                                if other_data.get("uuid") == new_uuid:
                                    return jsonify({'success': False, 'message': 'HATA: Bu UUID (Kimlik) zaten başka bir Node tarafından kullanılıyor! Aynı kimlikle iki node çalıştırılamaz.'})
                        except:
                            pass
        except:
            final_content = content
            
        with open(os.path.join(node_path, 'user-preferences.json'), 'w') as f:
            f.write(final_content)
        
        return jsonify({'success': True, 'message': 'UUID başarıyla kaydedildi.'})
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
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, port_prefix FROM nodes WHERE auto_restart = 1')
            monitored_nodes = c.fetchall()
            conn.close()
            
            for row in monitored_nodes:
                node_id = row[0]
                port_prefix = row[1]
                container_name = f'pi-node-{node_id}'
                node_path = os.path.join(NODES_DIR, node_id)
                
                # Check if uuid exists, if not, skip
                uuid_path = os.path.join(node_path, 'user-preferences.json')
                if not os.path.exists(uuid_path) or os.path.getsize(uuid_path) < 10:
                    continue
                
                # Check container status
                try:
                    docker_out = subprocess.check_output(['docker', 'ps', '-f', f'name={container_name}', '--format', '{{.Status}}'], text=True).strip()
                    is_up = 'Up' in docker_out
                except:
                    is_up = False
                
                if not is_up:
                    # Container is down, start it
                    try:
                        subprocess.run(['docker', 'compose', 'up', '-d'], cwd=node_path)
                    except:
                        pass
                else:
                    # Container is up, check if API is hung (Erişilemiyor)
                    api_port = f"{port_prefix}1"
                    try:
                        r = requests.get(f'http://localhost:{api_port}/', timeout=3)
                        if r.status_code != 200:
                            raise Exception("Bad status")
                    except:
                        # API is dead, restart it!
                        try:
                            pass # Removed aggressive restart
                        except:
                            pass
        except Exception as e:
            print("Watchdog error:", e)
            
        time.sleep(60)

# Start watchdog thread
threading.Thread(target=watchdog_loop, daemon=True).start()


latest_available_image = None
is_updating = False

def check_updates_loop():
    global latest_available_image, is_updating
    while True:
        try:
            r = requests.get("https://hub.docker.com/v2/repositories/pinetwork/pi-node-docker/tags?page_size=10", timeout=10)
            tags = [t['name'] for t in r.json().get('results', []) if 'mainnet' in t['name'] and 'RC' not in t['name']]
            if tags:
                tags.sort(reverse=True)
                latest_tag = tags[0]
                latest_image = f"pinetwork/pi-node-docker:{latest_tag}"
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key='target_image'")
                res = c.fetchone()
                current_image = res[0] if res else None
                
                c.execute("SELECT value FROM settings WHERE key='auto_update'")
                res2 = c.fetchone()
                auto_update = res2[0] if res2 else '0'
                conn.close()
                
                if current_image and latest_image != current_image:
                    latest_available_image = latest_image
                    if auto_update == '1' and not is_updating:
                        print("Auto-update triggered for:", latest_image)
                        trigger_global_update(latest_image)
                else:
                    latest_available_image = None
        except Exception as e:
            print("Update check error:", e)
        time.sleep(3600)

threading.Thread(target=check_updates_loop, daemon=True).start()

def trigger_global_update(new_image):
    global is_updating
    is_updating = True
    threading.Thread(target=_perform_global_update, args=(new_image,), daemon=True).start()

def _perform_global_update(new_image):
    global is_updating, latest_available_image
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id FROM nodes")
        nodes = c.fetchall()
        
        c.execute("UPDATE settings SET value=? WHERE key='target_image'", (new_image,))
        conn.commit()
        conn.close()
        
        for node in nodes:
            node_id = node[0]
            compose_path = os.path.join(NODES_DIR, node_id, 'docker-compose.yml')
            if os.path.exists(compose_path):
                with open(compose_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                import re
                content = re.sub(r'image:\s*pinetwork/pi-node-docker:.*', f'image: {new_image}', content)
                
                with open(compose_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                print(f"Updating node {node_id} to {new_image}")
                import subprocess
                subprocess.run(['docker', 'compose', 'pull'], cwd=os.path.join(NODES_DIR, node_id))
                subprocess.run(['docker', 'compose', 'up', '-d'], cwd=os.path.join(NODES_DIR, node_id))
        
        latest_available_image = None
    except Exception as e:
        print("Update error:", e)
    finally:
        is_updating = False

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
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
        "update_available": latest_available_image,
        "is_updating": is_updating
    })

@app.route('/api/update_all', methods=['POST'])
def api_update_all():
    global latest_available_image
    if latest_available_image and not is_updating:
        trigger_global_update(latest_available_image)
        return jsonify({"success": True, "msg": "Güncelleme başlatıldı!"})
    return jsonify({"success": False, "msg": "Güncellenecek sürüm yok veya zaten güncelleniyor."})



def check_password(pwd):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='admin_password'")
    res = c.fetchone()
    conn.close()
    if res and res[0]:
        return pwd == res[0]
    return pwd == '435102'

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
