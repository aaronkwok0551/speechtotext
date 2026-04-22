import os
import subprocess
import logging
import traceback
from flask import Flask, request, jsonify, render_template_string
import requests
from openai import OpenAI, RateLimitError
from werkzeug.utils import secure_filename

# 設定日誌，讓 Railway 的 Deploy Logs 可以看到詳細資訊
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            status.innerText = '⏳ 正在處理中（轉碼 -> 聽寫 -> 潤飾）...';
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
                status.innerText = '❌ 連線失敗，請檢查網路或 Railway Logs。';
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
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"conv_{filename}.mp3")
    
    try:
        logger.info(f"--- 開始處理檔案: {filename} ---")
        file.save(input_path)
        
        # 1. FFmpeg 壓縮
        logger.info("步驟 1: FFmpeg 轉碼中...")
        subprocess.run(['ffmpeg', '-i', input_path, '-y', '-ar', '16000', '-ac', '1', '-b:a', '32k', output_path], 
                       check=True, capture_output=True)
        
        # 2. Groq 聽寫
        logger.info("步驟 2: 傳送到 Groq (Whisper-large-v3)...")
        groq_key = os.environ.get("GROQ_API_KEY")
        if not groq_key: raise ValueError("缺少 GROQ_API_KEY 環境變數")
        
        # 關閉 max_retries，避免遇到 429 錯誤時伺服器無限空轉等待
        client = OpenAI(
            api_key=groq_key, 
            base_url="https://api.groq.com/openai/v1",
            max_retries=0 
        )
        
        whisper_prompt = (
            "這是一段香港政府保安局禁毒處、房屋局的會議紀錄。"
            "請用繁體中文及正確術語。"
        )
        
        with open(output_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                prompt=whisper_prompt,
                response_format="text"
            )
        logger.info("Groq 聽寫完成。")

        # 3. OpenRouter 潤飾
        logger.info("步驟 3: 傳送到 OpenRouter (Qwen-plus) 潤飾...")
        open_key = os.environ.get("OPENROUTER_API_KEY")
        if not open_key: raise ValueError("缺少 OPENROUTER_API_KEY 環境變數")

        ai_resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {open_key}",
                "HTTP-Referer": "https://railway.app", 
            },
            json={
                "model": "qwen/qwen-plus",
                "messages": [
                    {
                        "role": "system", 
                        "content": "你是一位香港政府高級行政主任(SEO)。請將以下內容整理成專業書面語，去除重複贅字。"
                    },
                    {"role": "user", "content": transcription}
                ]
            },
            timeout=120
        )
        
        if ai_resp.status_code != 200:
            logger.error(f"OpenRouter 錯誤: {ai_resp.text}")
            return jsonify({'error': f'OpenRouter 回應異常: {ai_resp.status_code}'}), 500

        final_text = ai_resp.json()['choices'][0]['message']['content']
        logger.info("--- 所有流程順利結束 ---")
        return jsonify({'text': final_text})

    except RateLimitError as e:
        logger.warning(f"Groq 額度超限: {str(e)}")
        return jsonify({'error': 'Groq 語音轉文字的每小時免費額度已滿，請等待大約 10 分鐘後再試。'}), 429
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg 失敗: {e.stderr.decode() if e.stderr else str(e)}")
        return jsonify({'error': '錄音檔格式轉換失敗，請確保上傳的是有效的音訊檔。'}), 500
    except Exception as e:
        logger.error("系統發生異常!! 詳細追蹤如下:")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'系統異常: {str(e)}'}), 500
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
