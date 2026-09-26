FROM python:3.14-slim
WORKDIR /app
COPY stocksense ./stocksense
COPY web ./web
COPY run.py .
RUN useradd --create-home stocksense && mkdir data && chown stocksense:stocksense data
USER stocksense
ENV HOST=0.0.0.0 PORT=8000 STOCKSENSE_DB=/app/data/stocksense.db
EXPOSE 8000
VOLUME ["/app/data"]
CMD ["python", "run.py"]
