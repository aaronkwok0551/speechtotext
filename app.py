import os
import subprocess
from flask import Flask, request, jsonify, render_template_string
from groq import Groq
import requests
from werkzeug.utils import secure_filename
import imageio_ffmpeg  # 呼叫我們自帶的免安裝版 FFmpeg

app = Flask(__name__)
# 判斷是否在 Railway 環境，若是則使用 /tmp 暫存區避免權限問題
app.config['UPLOAD_FOLDER'] = '/tmp' if os.environ.get('RAILWAY_ENVIRONMENT') else 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 網頁介面 (已經幫你優化了等待提示)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 政府公文語音助手</title>
    <style>
        body { font-family: sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; line-height: 1.6; color: #333; }
        .container { border: 1px solid #ddd; padding: 25px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        input[type="file"] { margin: 20px 0; display: block; }
        button { background: #0056b3; color: white; border: none; padding: 12px 24px; border-radius: 5px; cursor: pointer; font-size: 16px; font-weight: bold; }
        button:disabled { background: #ccc; cursor: not-allowed; }
        #status { margin-top: 15px; font-weight: bold; color: #d9534f; }
        #result { white-space: pre-wrap; background: #f8f9fa; padding: 20px; margin-top: 20px; border-radius: 8px; border: 1px solid #eee; display: none; font-size: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🎙️ AI 政府公文語音助手</h2>
        <p>請上傳廣東話錄音（支援現場視察、會議紀錄，如 60 分鐘立法會錄音），系統將自動轉譯並排版成標準中文書面語公文。</p>
        <input type="file" id="audioFile" accept="audio/*">
        <button id="submitBtn" onclick="uploadFile()">開始生成公文</button>
        <div id="status"></div>
        <div id="result"></div>
    </div>

    <script>
        async function uploadFile() {
            const fileInput = document.getElementById('audioFile');
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            const btn = document.getElementById('submitBtn');
            
            if (!fileInput.files[0]) return alert('請先選擇錄音檔案！');

            const formData = new FormData();
            formData.append('audio', fileInput.files[0]);

            btn.disabled = true;
            // 特別提醒長錄音的等待時間
            status.innerHTML = '⏳ 處理中...<br><span style="font-size:14px; color:#666;">（如上傳 1 小時錄音，請耐心等待約 1 至 2 分鐘，請勿關閉網頁）</span>';
            result.style.display = 'none';

            try {
                const response = await fetch('/upload', { method: 'POST', body: formData });
                const data = await response.json();
                
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 轉換完成！';
                    status.style.color = '#28a745';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                }
            } catch (e) {
                status.innerText = '❌ 發生連線錯誤或等待超時。';
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
        # 儲存原始檔案
        file.save(input_path)
        
        # 1. 取得自帶的 FFmpeg 武器，並執行極限壓縮 (加入 -b:a 32k 確保長錄音不會超過 API 限制)
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [ffmpeg_exe, '-i', input_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', output_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        
        # 2. 呼叫 Groq API 進行極速聽寫
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        with open(output_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(output_path), audio_file.read()),
                model="whisper-large-v3",
                prompt="這是一段香港廣東話對話，包含政府行政、房屋局、保安局禁毒處或立法會相關內容。",
                response_format="json"
            )
        transcript_text = transcription.text

        # 3. 呼叫 OpenRouter (Qwen-Plus) 進行公文潤飾
        headers = {
            "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}",
            "HTTP-Referer": "https://github.com/ai-transcriber", 
            "Content-Type": "application/json"
        }
        payload = {
            "model": "qwen/qwen-plus",
            "messages": [
                {
                    "role": "system", 
                    "content": "你是一位香港政府高級行政主任。請將以下廣東話逐字稿整理成嚴謹、專業的中文書面語公文格式，去除口語贅字，確保語氣得體，適合房屋局或保安局等部門的正式紀錄、技術審核回覆或內部報告。"
                },
                {"role": "user", "content": transcript_text}
            ]
        }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        final_text = response.json()['choices'][0]['message']['content']

        return jsonify({'text': final_text})

    except Exception as e:
        print(f"Error processing file: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        # 清除暫存檔，保持伺服器乾淨
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
