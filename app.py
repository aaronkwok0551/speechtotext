import os
import subprocess
import time
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
    <title>AI 粵語公文助理 - 穩定版</title>
    <style>
        body { font-family: sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; background-color: #f0f2f5; }
        .card { background: white; padding: 30px; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); }
        button { background: #1a73e8; color: white; border: none; padding: 14px; border-radius: 8px; cursor: pointer; width: 100%; font-size: 16px; }
        button:disabled { background: #bdc3c7; }
        #status { margin: 20px 0; font-weight: bold; text-align: center; }
        #result { white-space: pre-wrap; background: #f8f9fa; padding: 20px; border-radius: 8px; border: 1px solid #dadce0; display: none; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🎙️ AI 粵語轉公文 (穩定重試版)</h2>
        <input type="file" id="audioFile" accept="audio/*">
        <button id="submitBtn" onclick="process()">開始生成</button>
        <div id="status"></div>
        <div id="result"></div>
    </div>
    <script>
        async function process() {
            const file = document.getElementById('audioFile').files[0];
            if (!file) return alert('請選擇檔案');
            const btn = document.getElementById('submitBtn');
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            btn.disabled = true;
            status.innerText = '⏳ 處理中，請耐心等候...';
            result.style.display = 'none';
            try {
                const res = await fetch('/upload', { method: 'POST', body: new FormData().append('audio', file) || new FormData() });
                // 修正 FormData 傳遞
                const fd = new FormData(); fd.append('audio', file);
                const response = await fetch('/upload', { method: 'POST', body: fd });
                const data = await response.json();
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 成功';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                }
            } catch (e) { status.innerText = '❌ 連線異常，請查看 Railway Logs'; }
            finally { btn.disabled = false; }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('audio')
    if not file: return jsonify({'error': 'No file'}), 400
    
    in_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    out_path = in_path + ".mp3"
    
    try:
        file.save(in_path)
        # 轉碼
        subprocess.run(['ffmpeg', '-i', in_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', out_path], check=True)
        
        # --- Groq 聽寫 (帶重試機制) ---
        transcript_text = ""
        for i in range(3): # 最多試 3 次
            try:
                client = Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=300.0)
                with open(out_path, "rb") as f:
                    ts = client.audio.transcriptions.create(file=(out_path, f.read()), model="whisper-large-v3", language="zh")
                transcript_text = ts.text
                break
            except Exception as e:
                if i == 2: raise Exception(f"Groq 連線失敗: {str(e)}")
                time.sleep(2)

        # --- OpenRouter 潤飾 (帶重試機制) ---
        api_key = os.environ.get("OPENROUTER_API_KEY")
        final_content = ""
        for i in range(3):
            try:
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "qwen/qwen-plus",
                        "messages": [
                            {"role": "system", "content": "你是一位專業的香港政府行政主任。請將錄音逐字稿整理成嚴謹的書面語公文。"},
                            {"role": "user", "content": transcript_text}
                        ]
                    },
                    timeout=300.0
                )
                resp.raise_for_status()
                final_content = resp.json()['choices'][0]['message']['content']
                break
            except Exception as e:
                if i == 2: raise Exception(f"AI 整理失敗: {str(e)}")
                time.sleep(2)

        return jsonify({'text': final_content})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(in_path): os.remove(in_path)
        if os.path.exists(out_path): os.remove(out_path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
