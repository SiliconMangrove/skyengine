FROM node:22-alpine

WORKDIR /app

COPY application/frontend/package.json application/frontend/package-lock.json ./
RUN npm ci

COPY application/frontend ./

EXPOSE 5173

CMD ["npm", "run", "dev"]
