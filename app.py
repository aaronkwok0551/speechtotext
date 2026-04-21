import os
import subprocess
from flask import Flask, request, jsonify, render_template_string
from groq import Groq
import requests
from werkzeug.utils import secure_filename
import imageio_ffmpeg

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp' if os.environ.get('RAILWAY_ENVIRONMENT') else 'uploads'

# 確保上傳資料夾存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 簡單的 HTML 前端介面
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 語音轉公文助手</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; line-height: 1.6; }
        .container { border: 1px solid #ddd; padding: 20px; border-radius: 8px; }
        input { margin: 20px 0; }
        button { background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; }
        button:disabled { background: #ccc; }
        #result { white-space: pre-wrap; background: #f4f4f4; padding: 15px; margin-top: 20px; border-radius: 5px; display: none; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🎙️ AI 語音轉公文助手</h2>
        <p>上傳廣東話錄音（如現場視察或會議紀錄），自動生成書面語公文。</p>
        <input type="file" id="audioFile" accept="audio/*">
        <button id="submitBtn" onclick="uploadFile()">開始轉換</button>
        <div id="status" style="margin-top: 10px; font-weight: bold;"></div>
        <div id="result"></div>
    </div>

    <script>
        async function uploadFile() {
            const fileInput = document.getElementById('audioFile');
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            const btn = document.getElementById('submitBtn');
            
            if (!fileInput.files[0]) return alert('請先選擇檔案');

            const formData = new FormData();
            formData.append('audio', fileInput.files[0]);

            btn.disabled = true;
            status.innerText = '處理中... (正在轉碼並請求 AI，請稍候)';
            result.style.display = 'none';

            try {
                const response = await fetch('/upload', { method: 'POST', body: formData });
                const data = await response.json();
                
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 轉換完成！';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                }
            } catch (e) {
                status.innerText = '❌ 發生連線錯誤。';
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
    if file.filename == '':
        return jsonify({'error': '未選擇檔案'}), 400

    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{filename}.mp3")
    
    try:
        file.save(input_path)
        
        # 1. 使用 FFmpeg 轉檔為 mp3 1. 取得 Python 版 FFmpeg 的絕對路徑
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        # 使用自帶的 FFmpeg 轉檔 (加入 -b:a 32k 確保 60 分鐘音檔不會超過 25MB 限制)
        subprocess.run([ffmpeg_exe, '-i', input_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', output_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 2. 呼叫 Groq API (Whisper) 產生逐字稿
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        with open(output_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(output_path), audio_file.read()),
                model="whisper-large-v3",
                prompt="這是一段廣東話對話。",
                response_format="json"
            )
        transcript_text = transcription.text

        # 3. 呼叫 OpenRouter API (Qwen Plus) 整理公文
        headers = {
            "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}",
            "HTTP-Referer": "https://github.com/YourUsername/ai-transcriber", 
            "Content-Type": "application/json"
        }
        payload = {
            "model": "qwen/qwen-plus",
            "messages": [
                {
                    "role": "system", 
                    "content": "你是一位香港政府高級秘書。請將以下廣東話逐字稿整理成嚴謹、專業的中文書面語公文格式，確保語氣得體，適合房屋局或保安局等政府部門的內部紀錄或正式回覆。"
                },
                {"role": "user", "content": transcript_text}
            ]
        }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        final_text = response.json()['choices'][0]['message']['content']

        return jsonify({'text': final_text})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # 清理伺服器上的暫存檔案
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
