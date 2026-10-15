FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /workspace/savehub
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN mkdir -p data
ENV SAVEHUB_DB=/workspace/savehub/data/savehub.db
# Render injects $PORT; default 10000 for plain `docker run -p 10000:10000`
ENV PORT=10000
EXPOSE 10000
CMD ["python", "-m", "savehub.server"]
