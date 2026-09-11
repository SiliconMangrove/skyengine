FROM node:22-alpine

WORKDIR /app

COPY application/frontend/package.json application/frontend/package-lock.json ./
RUN npm ci

COPY application/frontend ./

# Keep npm command shims executable even when the build context came from a
# filesystem that does not preserve Unix executable bits.
RUN chmod +x node_modules/.bin/*

EXPOSE 5173

# 启动时注入 RAG_BACKEND_URL 到 index.html
CMD RAG_BACKEND_URL="${RAG_BACKEND_URL:-}" && \
    if [ -f index.html ]; then \
      sed -i "s|__RAG_VALUE__|${RAG_BACKEND_URL}|g" index.html; \
    fi && \
    npm run dev
