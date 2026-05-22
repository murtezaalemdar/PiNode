import json
import paramiko
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Sunucu Bilgileri
SERVER_IP = '192.168.0.10'
SERVER_USER = 'root'
SERVER_PASS = '435102'

def update_remote_node(content):
    try:
        # Check if it's valid JSON, if not, treat it as raw text
        try:
            parsed = json.loads(content)
            final_content = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            # It's not JSON, probably raw UUID/encrypted string. Just save as is.
            final_content = content.strip()
            
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(SERVER_IP, username=SERVER_USER, password=SERVER_PASS, timeout=10)
        
        # Klasörün var olduğundan emin ol
        ssh.exec_command('mkdir -p /root/.pi-node-cli')
        
        # SFTP ile dosyayı yaz
        sftp = ssh.open_sftp()
        remote_path = '/root/.pi-node-cli/user-preferences.json'
        with sftp.file(remote_path, 'w') as f:
            f.write(final_content)
        sftp.close()
        
        # Node container'ını yeniden başlat
        ssh.exec_command('docker restart mainnet')
        
        ssh.close()
        return True, "Yapılandırma başarıyla sunucuya kaydedildi ve Node yeniden başlatıldı!"
    except json.JSONDecodeError:
        return False, "Geçersiz JSON formatı. Lütfen kopyaladığınız içeriği kontrol edin."
    except Exception as e:
        return False, f"Sunucu bağlantı hatası: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/save', methods=['POST'])
def save_config():
    data = request.json
    content = data.get('content', '')
    if not content.strip():
        return jsonify({'success': False, 'message': 'Lütfen JSON içeriğini yapıştırın!'})
        
    success, message = update_remote_node(content)
    return jsonify({'success': success, 'message': message})

if __name__ == '__main__':
    # Tarayıcının otomatik olarak açılabilmesi için ufak bir gecikme ekliyoruz
    import threading
    import webbrowser
    import time
    
    def open_browser():
        time.sleep(1.5)
        webbrowser.open_new("http://127.0.0.1:5000")
        
    threading.Thread(target=open_browser).start()
    app.run(debug=False, port=5000)
