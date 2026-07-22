FROM node:20-alpine

# System deps for sharp (QR image generation)
RUN apk add --no-cache python3 make g++ vips-dev git

WORKDIR /app
COPY package.json .
RUN npm install --omit=dev

COPY . .

# Auth session persisted via volume
VOLUME ["/app/auth_info"]

CMD ["node", "index.js"]
