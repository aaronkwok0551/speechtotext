# 1. 使用穩定的 Python 3.11 版本
FROM python:3.11-slim

# 2. 強制安裝系統級別的 ffmpeg (這是最關鍵的一步)
RUN apt-get update && apt-get install -y ffmpeg && apt-get clean

# 3. 設定工作目錄
WORKDIR /app

# 4. 複製套件清單並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 複製所有程式碼
COPY . .

# 6. 啟動程式 (配合 600 秒超時，應付立法會長錄音)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "600", "app:app"]
