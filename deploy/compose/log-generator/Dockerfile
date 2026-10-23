FROM python:3.12-slim
RUN useradd --system --uid 10001 app
COPY generator.py /app/generator.py
USER 10001
EXPOSE 8000
CMD ["python", "-u", "/app/generator.py"]
