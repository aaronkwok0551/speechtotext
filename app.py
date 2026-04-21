import os
import subprocess
from flask import Flask, request, jsonify, render_template_string
import requests
from openai import OpenAI
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <title>AI 粵語公文助理 - 專業版</title>
    <style>
        body { font-family: -apple-system, system-ui, sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; line-height: 1.6; background-color: #f0f2f5; }
        .card { background: white; padding: 30px; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); }
        h2 { color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 10px; margin-top: 0; }
        button { background: #1a73e8; color: white; border: none; padding: 14px; border-radius: 8px; cursor: pointer; width: 100%; font-size: 16px; margin-top: 10px; font-weight: bold; }
        button:disabled { background: #bdc3c7; }
        #status { margin-top: 20px; color: #5f6368; font-weight: bold; text-align: center; }
        #result { white-space: pre-wrap; background: #f8f9fa; padding: 20px; margin-top: 20px; border-radius: 8px; border: 1px solid #dadce0; display: none; font-size: 15px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🎙️ 行政公文語音助手</h2>
        <p>上傳錄音（支援 m4a/mp3），自動生成政府格式書面語報告。</p>
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
            status.innerText = '⏳ 正在轉碼與 AI 分析中...（請耐心等候）';
            result.style.display = 'none';

            const fd = new FormData();
            fd.append('audio', file);

            try {
                const response = await fetch('/upload', { method: 'POST', body: fd });
                const data = await response.json();
                if (response.ok) {
                    result.innerText = data.text;
                    result.style.display = 'block';
                    status.innerText = '✅ 生成成功！';
                } else {
                    status.innerText = '❌ 錯誤：' + data.error;
                }
            } catch (e) {
                status.innerText = '❌ 連線逾時，請檢查網絡。';
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
        
        # 1. FFmpeg 壓縮 (維持 32k 以確保長錄音穩定傳輸)
        subprocess.run(['ffmpeg', '-i', input_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', output_path], check=True)
        
        # 2. 使用 OpenAI SDK 套殼連線 Groq
        client = OpenAI(
            api_key=os.environ.get("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )
        
        # --- 重點優化：Prompt 提示詞 ---
        # 這裡加入了你常處理的專有名詞，引導 AI 使用繁體中文及正確術語
        whisper_prompt = (
            "這是一段香港政府保安局禁毒處、房屋局的會議紀錄。內容包含：獨立審查組(ICU)、"
            "穗禾苑、安基苑、宏福苑、屋邨維修、棚架安全、啟德體育園、抗毒宣傳、"
            "冰毒、及政府行政公文用語。標點符號：，。！？"
        )
        
        with open(output_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                prompt=whisper_prompt,  # 注入優化後的提示
                response_format="text"
            )
        
        # 3. 呼叫 OpenRouter 進行 EO 級別潤飾
        open_key = os.environ.get("OPENROUTER_API_KEY")
        ai_resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {open_key}"},
            json={
                "model": "qwen/qwen-plus",
                "messages": [
                    {
                        "role": "system", 
                        "content": "你是一位香港政府高級行政主任(SEO/EO)。請將以下錄音內容整理成專業的中文書面語公文，"
                                   "採用政府公函或內部會議紀錄格式，去除重複贅字，用詞需嚴謹莊重。若提及屋苑維修或禁毒事宜，請確保術語準確。"
                    },
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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
