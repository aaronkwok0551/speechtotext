import os
import subprocess
from flask import Flask, request, jsonify, render_template_string
import requests
from openai import OpenAI  # 借用 OpenAI 的穩定協議
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 粵語公文助理 - 成功穩定版</title>
    <style>
        body { font-family: sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; line-height: 1.6; background-color: #f0f2f5; }
        .card { background: white; padding: 30px; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); }
        h2 { color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 10px; }
        button { background: #1a73e8; color: white; border: none; padding: 14px; border-radius: 8px; cursor: pointer; width: 100%; font-size: 16px; margin-top: 10px; }
        button:disabled { background: #bdc3c7; }
        #status { margin-top: 20px; color: #5f6368; font-weight: bold; text-align: center; }
        #result { white-space: pre-wrap; background: #f8f9fa; padding: 20px; margin-top: 20px; border-radius: 8px; border: 1px solid #dadce0; display: none; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🎙️ AI 粵語轉公文助理</h2>
        <p>上傳錄音檔（支援 60 分鐘長度），系統將自動轉為標準書面語報告。</p>
        <input type="file" id="audioFile" accept="audio/*">
        <button id="submitBtn" onclick="process()">開始生成公文</button>
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
            status.innerText = '⏳ 正在處理中...（請耐心等候 1-3 分鐘）';
            result.style.display = 'none';

            const fd = new FormData();
            fd.append('audio', file);

            try {
                const response = await fetch('/upload', { method: 'POST', body: fd });
                const data = await response.json();
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 處理完成！';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                }
            } catch (e) {
                status.innerText = '❌ 伺服器忙碌或連線超時。';
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
    file = request.files.get('audio')
    if not file: return jsonify({'error': '未上傳檔案'}), 400
    
    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"final_{filename}.mp3")
    
    try:
        file.save(input_path)
        
        # 1. FFmpeg 轉碼與壓縮 (參考新聞系統邏輯)
        subprocess.run(['ffmpeg', '-i', input_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', output_path], check=True)
        
        # 2. 模仿新聞系統成功的 Groq 調用方式 (使用 OpenAI 客戶端套殼)
        client = OpenAI(
            api_key=os.environ.get("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )
        
        with open(output_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                prompt="這是一段香港政府會議、禁毒處、或房屋局的廣東話紀錄。請精準聽寫。",
                response_format="text"
            )
        
        # 3. 呼叫 OpenRouter 進行公文潤飾
        open_key = os.environ.get("OPENROUTER_API_KEY")
        ai_resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {open_key}"},
            json={
                "model": "qwen/qwen-plus",
                "messages": [
                    {"role": "system", "content": "你是一位香港政府行政主任(EO)。請將以下錄音整理成專業的中文書面語公文，去除贅字，確保語氣莊重。"},
                    {"role": "user", "content": transcription}
                ]
            },
            timeout=300
        )
        
        final_text = ai_resp.json()['choices'][0]['message']['content']
        return jsonify({'text': final_text})

    except Exception as e:
        return jsonify({'error': f'系統異常: {str(e)}'}), 500
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == '__main__':
    # 這裡配合 Railway 的 PORT 設定
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
