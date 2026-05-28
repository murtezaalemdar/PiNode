from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import json
import subprocess
import requests
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
app.secret_key = 'pi-node-fleet-secret-key-2026'

PASSWORD = '435102'
BASE_DIR = '/opt/pi-fleet'

# In-memory capsule names (persisted to file)
NAMES_FILE = os.path.join(BASE_DIR, 'capsule_names.json')

def load_names():
    try:
        with open(NAMES_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_names(names):
    with open(NAMES_FILE, 'w') as f:
        json.dump(names, f)

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
    if not is_authenticated(): return redirect(url_for('login'))
    from flask import make_response
    response = make_response(render_template('index.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

import threading
import time

global_disk_busy = "Hesaplanıyor..."

def disk_io_monitor():
    global global_disk_busy
    try:
        import psutil
        io1 = psutil.disk_io_counters()
        while True:
            time.sleep(2)
            io2 = psutil.disk_io_counters()
            if hasattr(io1, 'busy_time') and hasattr(io2, 'busy_time'):
                busy_delta = io2.busy_time - io1.busy_time
                busy_pct = min(100.0, (busy_delta / 20.0))
                global_disk_busy = f"{busy_pct:.1f}%"
            else:
                global_disk_busy = "Desteklenmiyor"
            io1 = io2
    except:
        global_disk_busy = "Hata"

threading.Thread(target=disk_io_monitor, daemon=True).start()

@app.route('/api/system')
def system_stats():
    import psutil
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    try:
        temps = psutil.sensors_temperatures()
        if 'coretemp' in temps and len(temps['coretemp']) > 0:
            temp = f"{temps['coretemp'][0].current}°C"
        elif temps:
            first_key = list(temps.keys())[0]
            temp = f"{temps[first_key][0].current}°C"
        else:
            temp = "Bilinmiyor"
    except:
        temp = "Desteklenmiyor"

    return jsonify({
        'cpu': f"{cpu}%",
        'ram_used': f"{ram.used // (1024**3)} GB",
        'ram_total': f"{ram.total // (1024**3)} GB",
        'ram_percent': f"{ram.percent}%",
        'disk_used': f"{disk.used // (1024**3)} GB",
        'disk_total': f"{disk.total // (1024**3)} GB",
        'disk_percent': f"{disk.percent}%",
        'public_ip': f"{cached_public_ip} (Çoklu IP Aktif)",
        'temp': temp,
        'disk_busy': global_disk_busy
    })

@app.route('/api/nodes', methods=['GET'])
def get_nodes():
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    
    names = load_names()
    nodes = []
    for i in range(1, 7):
        node_id = str(i)
        container_name = f'pinode_capsule_{i}'
        name = names.get(node_id, f"Kapsül {i}")
        
        port_prefix = f"314{i}" if i != 2 else "3140"
        
        try:
            docker_out = subprocess.check_output(['docker', 'ps', '-f', f'name=^{container_name}$', '--format', '{{.Status}}'], text=True, timeout=5).strip()
            status = docker_out if docker_out else "Durduruldu"
        except Exception:
            status = "Hata"
            
        uuid_val = "Tanımsız"
        sync_status = "Erişilemiyor"
        state = "-"
        incoming = 0
        detail = ""
        
        is_up = "Up" in status
        if is_up:
            # Get NODE_PRIVATE_KEY via docker inspect (fastest, no exec needed)
            try:
                out = subprocess.check_output(
                    f'docker exec {container_name} docker inspect mainnet --format "{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}"',
                    shell=True, text=True, timeout=5
                ).strip()
                for line in out.splitlines():
                    if line.startswith("NODE_PRIVATE_KEY="):
                        seed = line.split('=', 1)[1].strip()
                        if len(seed) > 10:
                            uuid_val = f"Kayıtlı ({seed[:10]}...)"
                        break
            except:
                pass

            # Get sync status via stellar-core /info (more reliable than Horizon)
            try:
                out = subprocess.check_output(
                    f'docker exec {container_name} docker exec mainnet curl -s http://localhost:11626/info',
                    shell=True, text=True, timeout=8
                )
                core_data = json.loads(out).get('info', {})
                state = core_data.get('state', '-')
                ledger = core_data.get('ledger', {}).get('num', 0)
                if ledger > 0:
                    sync_status = f"Ledger: {ledger:,}"
                peers_info = core_data.get('peers', {})
                incoming = peers_info.get('authenticated_count', 0)
                # Get detail status
                status_arr = core_data.get('status', [])
                detail = ""
                if status_arr:
                    detail = status_arr[0]
                
                if state == "Catching up" and detail:
                    import re
                    match = re.search(r'Catching up to ledger (\d+)', detail)
                    if match:
                        target_ledger = int(match.group(1))
                        sync_status = f"Ledger: {ledger:,} / {target_ledger:,}"
            except:
                pass
            
        # Local Availability
        if is_up:
            availability = "100.0% (Aktif)"
        else:
            availability = "0.0% (Kapalı)"

        nodes.append({
            'id': node_id,
            'name': name,
            'port_prefix': port_prefix,
            'status': status,
            'uuid': uuid_val,
            'sync_status': sync_status,
            'state': state,
            'detail': detail,
            'protocol': '23',
            'incoming': incoming,
            'auto_restart': 1,
            'availability': availability
        })
    return jsonify(nodes)

@app.route('/api/nodes/<node_id>/action', methods=['POST'])
def node_action(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    action = request.json.get('action')
    try:
        container_name = f'pinode_capsule_{node_id}'
        if action == 'start':
            subprocess.run(f'docker start {container_name}', shell=True)
        elif action == 'stop':
            subprocess.run(f'docker stop {container_name}', shell=True)
        elif action == 'restart':
            subprocess.run(f'docker restart {container_name}', shell=True)
        elif action == 'wipe_db':
            # Stop mainnet, delete files on capsule host, start mainnet
            subprocess.run(
                f'docker exec {container_name} sh -c '
                f'"docker stop mainnet && rm -rf /root/pi-node/docker_volumes/mainnet/history/* /root/pi-node/docker_volumes/mainnet/stellar/* && docker start mainnet"',
                shell=True
            )
        return jsonify({'success': True, 'message': 'İşlem başarılı.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Hata: {str(e)}'})

@app.route('/api/nodes/<node_id>/uuid', methods=['GET'])
def get_uuid(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    try:
        container_name = f'pinode_capsule_{node_id}'
        out = subprocess.check_output(
            f'docker exec {container_name} docker inspect mainnet --format "{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}"',
            shell=True, text=True, timeout=5
        ).strip()
        seed = ""
        for line in out.splitlines():
            if line.startswith("NODE_PRIVATE_KEY="):
                seed = line.split('=', 1)[1].strip()
                break
        return jsonify({'success': True, 'content': seed})
    except:
        return jsonify({'success': True, 'content': ''})

@app.route('/api/nodes/<node_id>/set_seed', methods=['POST'])
def set_seed(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    seed = request.json.get('seed', '').strip()
    if not seed:
        return jsonify({'success': False, 'message': 'Seed boş olamaz.'})
    if not seed.startswith('S') or len(seed) < 50:
        return jsonify({'success': False, 'message': 'Geçersiz Seed formatı. S ile başlamalı ve en az 50 karakter olmalıdır.'})
        
    try:
        container_name = f'pinode_capsule_{node_id}'
        
        # Check if seed is already in use by another capsule
        for i in range(1, 7):
            if str(i) == node_id: continue
            try:
                other = f'pinode_capsule_{i}'
                out = subprocess.check_output(
                    f'docker exec {other} docker inspect mainnet --format "{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}"',
                    shell=True, text=True, timeout=5
                ).strip()
                for line in out.splitlines():
                    if line.startswith("NODE_PRIVATE_KEY=") and seed in line:
                        return jsonify({'success': False, 'message': f'HATA: Bu NODE_SEED zaten Kapsül {i} tarafından kullanılıyor!'})
            except:
                pass

        # Write new seed to .env file
        write_cmd = f'docker exec {container_name} docker exec pi-node-installer sh -c "cd /root/pi-node && sed -i \\"s|^NODE_PRIVATE_KEY=.*|NODE_PRIVATE_KEY={seed}|\\" .env"'
        result = subprocess.run(write_cmd, shell=True, capture_output=True, text=True)
        
        # Restart mainnet container to pick up new env
        restart_cmd = f'docker exec {container_name} docker exec pi-node-installer sh -c "cd /root/pi-node && docker compose down && docker compose up -d"'
        threading.Thread(target=lambda: subprocess.run(restart_cmd, shell=True), daemon=True).start()
        
        return jsonify({'success': True, 'message': 'Seed başarıyla kaydedildi ve Node arka planda yeniden başlatılıyor.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/nodes/<node_id>/rename', methods=['POST'])
def rename_node(node_id):
    if not is_authenticated(): return jsonify({'error': 'Unauthorized'}), 401
    new_name = request.json.get('name', '').strip()
    if not new_name: return jsonify({'success': False, 'message': 'İsim boş olamaz.'})
    try:
        names = load_names()
        names[node_id] = new_name
        save_names(names)
        return jsonify({'success': True, 'message': 'İsim güncellendi.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/settings', methods=['GET'])
def api_settings():
    return jsonify({
        "auto_update": "1",
        "current_image": "Kapsül DinD Mimari v2",
        "update_available": None,
        "is_updating": False
    })

# Watchdog: auto-restart crashed inner containers
def watchdog_loop():
    while True:
        try:
            for i in range(1, 7):
                cap = f'pinode_capsule_{i}'
                # Check if capsule itself is running
                try:
                    out = subprocess.check_output(
                        ['docker', 'ps', '-f', f'name=^{cap}$', '--format', '{{.Status}}'],
                        text=True, timeout=5
                    ).strip()
                    if 'Up' not in out:
                        # Start capsule
                        subprocess.run(f'docker start {cap}', shell=True)
                        time.sleep(10)
                except:
                    continue

                # Check if inner mainnet is running
                try:
                    inner_out = subprocess.check_output(
                        f'docker exec {cap} docker ps -f name=mainnet --format "{{{{.Status}}}}"',
                        shell=True, text=True, timeout=5
                    ).strip()
                    if 'Up' not in inner_out:
                        subprocess.run(f'docker exec {cap} docker start pi-node-installer mainnet', shell=True)
                        time.sleep(5)
                except:
                    pass

                # Auto-heal config (PEER_PORT and KNOWN_PEERS)
                try:
                    port = "31402" if i in [1, 2] else f"314{i}2"
                    patch_cmd = f"""docker exec {cap} docker exec mainnet bash -c '
                    changed=0
                    if ! grep -q "PEER_PORT = {port}" /opt/stellar/core/etc/stellar-core.cfg; then
                        if grep -q "PEER_PORT" /opt/stellar/core/etc/stellar-core.cfg; then
                            sed -i "s/^PEER_PORT.*/PEER_PORT = {port}/" /opt/stellar/core/etc/stellar-core.cfg
                        else
                            echo "PEER_PORT = {port}" >> /opt/stellar/core/etc/stellar-core.cfg
                        fi
                        changed=1
                    fi
                    if ! grep -q "KNOWN_PEERS" /opt/stellar/core/etc/stellar-core.cfg && [ "{i}" != "1" ]; then
                        sed -i "/^PEER_PORT/a KNOWN_PEERS=[\\"192.168.0.24:31402\\"]" /opt/stellar/core/etc/stellar-core.cfg
                        changed=1
                    fi
                    if [ "$changed" -eq 1 ]; then
                        supervisorctl restart stellar-core
                    fi
                    '"""
                    subprocess.run(patch_cmd, shell=True, timeout=10)
                except:
                    pass

        except:
            pass
        time.sleep(60)

threading.Thread(target=watchdog_loop, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3140)
