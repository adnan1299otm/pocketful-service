# Pocketful Service - Stage 1

## Build
```bash
docker build -t pocketful-service:stage-1 .
```

## Run
```bash
docker run -p 8080:8080 -e PORT=8080 pocketful-service:stage-1
```