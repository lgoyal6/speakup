FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY services services
RUN pip install --no-cache-dir -e .
EXPOSE 8080
