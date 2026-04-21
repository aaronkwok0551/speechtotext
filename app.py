import os
import subprocess
from flask import Flask, request, jsonify, render_template_string
from groq import Groq
import requests
from werkzeug.utils import secure_filename

app = Flask(__name__)
# Railway 環境建議使用 /tmp 資料夾處理臨時檔案
app.config['UPLOAD_FOLDER'] = '/tmp'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 粵語公文助理</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; line-height: 1.6; background-color: #f0f2f5; }
        .card { background: white; padding: 30px; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); }
        h2 { color: #1a73e8; margin-top: 0; }
        .input-group { margin: 20px 0; }
        input[type="file"] { display: block; margin-bottom: 20px; }
        button { background: #1a73e8; color: white; border: none; padding: 14px 28px; border-radius: 8px; cursor: pointer; font-size: 16px; width: 100%; transition: 0.3s; }
        button:hover { background: #1557b0; }
        button:disabled { background: #bdc3c7; cursor: not-allowed; }
        #status { margin-top: 20px; color: #5f6368; font-weight: bold; text-align: center; }
        #result { white-space: pre-wrap; background: #f8f9fa; padding: 20px; margin-top: 20px; border-radius: 8px; border: 1px solid #dadce0; display: none; font-size: 15px;}
    </style>
</head>
<body>
    <div class="card">
        <h2>🎙️ AI 粵語轉書面語公文</h2>
        <p>上傳會議或訪談錄音，自動生成正式書面語報告。</p>
        <div class="input-group">
            <input type="file" id="audioFile" accept="audio/*">
            <button id="submitBtn" onclick="processAudio()">開始生成報告</button>
        </div>
        <div id="status"></div>
        <div id="result"></div>
    </div>

    <script>
        async function processAudio() {
            const fileInput = document.getElementById('audioFile');
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            const btn = document.getElementById('submitBtn');
            
            if (!fileInput.files[0]) return alert('請先選擇錄音檔案');
            
            const formData = new FormData();
            formData.append('audio', fileInput.files[0]);
            
            btn.disabled = true;
            status.innerText = '⏳ 正在轉碼與 AI 處理中，長錄音請耐心等候 1-3 分鐘...';
            result.style.display = 'none';

            try {
                const response = await fetch('/upload', { method: 'POST', body: formData });
                const data = await response.json();
                
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 生成成功！';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                }
            } catch (e) {
                status.innerText = '❌ 連線逾時，請檢查伺服器日誌。';
            } finally {
                btn.disabled = false;
            }
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
    if 'audio' not in request.files:
        return jsonify({'error': '沒有上傳檔案'}), 400
    
    file = request.files['audio']
    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"processed_{filename}.mp3")
    
    try:
        # 儲存原始檔案
        file.save(input_path)
        
        # 1. FFmpeg 壓縮 (確保大小不超標)
        subprocess.run([
            'ffmpeg', '-i', input_path, 
            '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', 
            output_path
        ], check=True)
        
        # 2. Groq 聽寫 (設定 300 秒超時，防止 60 分鐘錄音斷線)
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=300.0)
        with open(output_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(output_path), audio_file.read()),
                model="whisper-large-v3",
                language="zh"
            )

        # 3. OpenRouter Qwen 潤飾 (設定 300 秒超時)
        api_key = os.environ.get("OPENROUTER_API_KEY")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "qwen/qwen-plus",
                "messages": [
                    {"role": "system", "content": "你是一位專業的香港政府行政主任(EO)。請將以下錄音逐字稿，在保留所有核心事實與數據的前提下，整理成一份嚴謹、用詞精確、語氣莊重的書面語報告。"},
                    {"role": "user", "content": transcription.text}
                ]
            },
            timeout=300.0
        )
        
        ai_content = response.json().get('choices', [{}])[0].get('message', {}).get('content', '處理失敗')
        return jsonify({'text': ai_content})

    except Exception as e:
        print(f"Error occurred: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        # 清理臨時檔案
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
