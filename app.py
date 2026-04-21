import os
import subprocess
import requests
from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 粵語公文助理 - 終極穩定版</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; line-height: 1.6; background-color: #f4f7f6; }
        .container { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
        h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
        button { background: #3498db; color: white; border: none; padding: 12px 25px; border-radius: 6px; cursor: pointer; width: 100%; font-size: 16px; margin-top: 10px; }
        button:disabled { background: #bdc3c7; }
        #status { margin-top: 15px; color: #e67e22; font-weight: bold; }
        #result { white-space: pre-wrap; background: #fff; padding: 20px; margin-top: 20px; border-radius: 8px; border-left: 5px solid #3498db; display: none; box-shadow: inset 0 0 10px rgba(0,0,0,0.05); }
    </style>
</head>
<body>
    <div class="container">
        <h2>🎙️ AI 粵語公文助理</h2>
        <p>上傳錄音檔（支援 10-60 分鐘長度），系統將自動生成專業書面語報告。</p>
        <input type="file" id="audioFile" accept="audio/*">
        <button id="submitBtn" onclick="uploadFile()">開始處理</button>
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
            status.innerText = '⏳ 正在轉碼並連線 AI 伺服器...（請耐心等候 1-2 分鐘）';
            result.style.display = 'none';
            try {
                const response = await fetch('/upload', { method: 'POST', body: formData });
                const data = await response.json();
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 生成完成！';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                    if(data.details) console.log(data.details);
                }
            } catch (e) {
                status.innerText = '❌ 網頁連線超時，但 AI 可能仍在後台處理中。';
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
    if not file: return jsonify({'error': '未找到檔案'}), 400
    
    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"final_{filename}.mp3")
    
    try:
        file.save(input_path)
        
        # 1. FFmpeg 壓縮 (32k 碼率)
        subprocess.run(['ffmpeg', '-i', input_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', output_path], check=True)
        
        # 2. 直接用 API Request 呼叫 Groq (不使用 SDK)
        groq_key = os.environ.get("GROQ_API_KEY")
        with open(output_path, "rb") as f:
            groq_response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": (os.path.basename(output_path), f)},
                data={"model": "whisper-large-v3", "language": "zh"},
                timeout=300
            )
        
        if groq_response.status_code != 200:
            return jsonify({'error': f'Groq API 報錯: {groq_response.text}'}), 500
        
        transcript_text = groq_response.json().get('text', '')

        # 3. 直接呼叫 OpenRouter
        open_key = os.environ.get("OPENROUTER_API_KEY")
        ai_response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {open_key}", "Content-Type": "application/json"},
            json={
                "model": "qwen/qwen-plus",
                "messages": [
                    {"role": "system", "content": "你是一位專業的香港政府行政主任(EO)。請將以下錄音整理成嚴謹、專業的中文書面語公文。"},
                    {"role": "user", "content": transcript_text}
                ]
            },
            timeout=300
        )
        
        if ai_response.status_code != 200:
            return jsonify({'error': f'OpenRouter 報錯: {ai_response.text}'}), 500
            
        final_text = ai_response.json()['choices'][0]['message']['content']
        return jsonify({'text': final_text})

    except Exception as e:
        return jsonify({'error': f'系統異常: {str(e)}'}), 500
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
