FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Create Streamlit config with CSIRO theme
RUN mkdir -p .streamlit && \
    printf '[theme]\nprimaryColor = "#00A9CE"\nbackgroundColor = "#FFFFFF"\nsecondaryBackgroundColor = "#F0F2F6"\ntextColor = "#00313C"\nfont = "sans serif"\n\n[server]\nheadless = true\n' > .streamlit/config.toml

EXPOSE 7860

HEALTHCHECK CMD curl --fail http://localhost:7860/_stcore/health

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
