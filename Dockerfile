# PHI PDF Studio as a public server.  docker build -t phi-pdf-studio .   (with Office → PDF: --build-arg OFFICE=1)
FROM python:3.12-slim
ARG OFFICE=0
ARG OCR_LANGS="eng ben hin ara"
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr $(for l in $OCR_LANGS; do echo tesseract-ocr-$l; done) \
      $( [ "$OFFICE" = "1" ] && echo "libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui fonts-noto-core" ) \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY pdfstudio ./pdfstudio
RUN pip install --no-cache-dir ".[ai]" && useradd --create-home --uid 10001 studio && mkdir /data && chown studio /data
USER studio
ENV PHIPDF_DATA=/data PHIPDF_PORT=8080 FORWARDED_ALLOW_IPS=*
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["phi-pdf", "serve", "--public"]
