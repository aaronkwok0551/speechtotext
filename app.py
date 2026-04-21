import os
import subprocess
from flask import Flask, request, jsonify, render_template_string
from groq import Groq
import requests
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp' if os.environ.get('RAILWAY_ENVIRONMENT') else 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 政府公文助手</title>
    <style>
        body { font-family: sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; line-height: 1.6; background-color: #f4f7f6; }
        .container { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
        h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
        button { background: #3498db; color: white; border: none; padding: 12px 25px; border-radius: 6px; cursor: pointer; font-size: 16px; transition: 0.3s; }
        button:hover { background: #2980b9; }
        button:disabled { background: #bdc3c7; }
        #status { margin-top: 15px; color: #7f8c8d; font-weight: bold; }
        #result { white-space: pre-wrap; background: #fff; padding: 20px; margin-top: 20px; border-radius: 8px; border-left: 5px solid #3498db; display: none; box-shadow: inset 0 0 10px rgba(0,0,0,0.05); }
    </style>
</head>
<body>
    <div class="container">
        <h2>🎙️ AI 政府公文語音助手</h2>
        <p>上傳任何錄音（支援 60 分鐘長錄音），自動生成專業書面語報告。</p>
        <input type="file" id="audioFile" accept="audio/*">
        <button id="submitBtn" onclick="uploadFile()">開始生成</button>
        <div id="status"></div>
        <div id="result"></div>
    </div>
    <script>
        async function uploadFile() {
            const fileInput = document.getElementById('audioFile');
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            const btn = document.getElementById('submitBtn');
            if (!fileInput.files[0]) return alert('請選擇檔案');
            const formData = new FormData();
            formData.append('audio', fileInput.files[0]);
            btn.disabled = true;
            status.innerText = '⏳ 處理中，請耐心等候...';
            result.style.display = 'none';
            try {
                const response = await fetch('/upload', { method: 'POST', body: formData });
                const data = await response.json();
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 處理完成';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                }
            } catch (e) {
                status.innerText = '❌ 連線超時，但 AI 可能仍在後台處理中。';
            } finally { btn.disabled = false; }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files['audio']
    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{filename}.mp3")
    
    try:
        file.save(input_path)
        
        # 1. 呼叫系統安裝的 FFmpeg，強制壓縮
        subprocess.run(['ffmpeg', '-i', input_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', output_path], check=True)
        
        # 2. Groq 聽寫
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        with open(output_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(output_path), audio_file.read()),
                model="whisper-large-v3",
                language="zh"
            )

        # 3. OpenRouter 潤飾
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", 
            headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}"},
            json={
                "model": "qwen/qwen-plus",
                "messages": [{"role": "system", "content": "你是一位香港政府高級秘書，請將以下廣東話整理成書面語公文。"},
                             {"role": "user", "content": transcription.text}]
            }
        )
        return jsonify({'text': response.json()['choices'][0]['message']['content']})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
