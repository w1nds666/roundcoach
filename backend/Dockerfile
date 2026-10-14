# 后端镜像：python:3.11（与计划书 4.2 锁定版本一致）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

# 6R19B 生产硬化：非 root 运行（降低解析/存储被利用时的爆炸半径）。
# PYTHONDONTWRITEBYTECODE=1 使 Python 不写 __pycache__（无需 /app 写权限）；临时文件写入 /tmp。
RUN addgroup --system app && adduser --system --ingroup app app && \
    chown -R app:app /app /tmp

EXPOSE 8000

USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
