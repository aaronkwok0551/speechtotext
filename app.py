import os
import subprocess
from flask import Flask, request, jsonify, render_template_string
from groq import Groq
import requests
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 粵語公文助理</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; line-height: 1.6; background: #f9f9f9; }
        .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
        h2 { color: #2c3e50; }
        button { background: #3b82f6; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; width: 100%; font-size: 16px; }
        button:disabled { background: #94a3b8; }
        #status { margin-top: 15px; color: #64748b; font-weight: bold; }
        #result { margin-top: 20px; white-space: pre-wrap; background: #f1f5f9; padding: 15px; border-radius: 8px; display: none; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🎙️ AI 粵語轉公文</h2>
        <p>上傳錄音檔（支援 60 分鐘長錄音），自動生成書面語報告。</p>
        <input type="file" id="audio" accept="audio/*">
        <button id="btn" onclick="process()">開始處理</button>
        <div id="status"></div>
        <div id="result"></div>
    </div>
    <script>
        async function process() {
            const file = document.getElementById('audio').files[0];
            if (!file) return alert('請選擇檔案');
            const btn = document.getElementById('btn');
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            
            btn.disabled = true;
            status.innerText = '⏳ 處理中...（長錄音請稍候 1-2 分鐘）';
            result.style.display = 'none';

            const fd = new FormData();
            fd.append('audio', file);
            try {
                const res = await fetch('/upload', { method: 'POST', body: fd });
                const data = await res.json();
                if (res.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 完成！';
                } else { status.innerText = '❌ 錯誤：' + data.error; }
            } catch (e) { status.innerText = '❌ 連線超時，請檢查日誌。'; }
            finally { btn.disabled = false; }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['audio']
    in_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    out_path = in_path + ".mp3"
    try:
        file.save(in_path)
        # 壓縮轉檔 (32k 碼率保證 1 小時音檔能傳給 Groq)
        subprocess.run(['ffmpeg', '-i', in_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', out_path], check=True)
        
        # Groq 聽寫
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        with open(output_path, "rb") as f:
            ts = client.audio.transcriptions.create(file=(out_path, f.read()), model="whisper-large-v3", language="zh")
        
        # OpenRouter 轉書面語
        res = requests.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}"},
            json={"model": "qwen/qwen-plus", "messages": [
                {"role": "system", "content": "你是一位香港政府行政主任，請將以下廣東話逐字稿整理成嚴謹的書面語報告。"},
                {"role": "user", "content": ts.text}
            ]}
        )
        return jsonify({'text': res.json()['choices'][0]['message']['content']})
    except Exception as e: return jsonify({'error': str(e)}), 500
    finally:
        for p in [in_path, out_path]:
            if os.path.exists(p): os.remove(p)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
