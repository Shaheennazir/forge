FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install -e .
EXPOSE 8000
CMD ["python", "-m", "{{ package_name }}.server"]
