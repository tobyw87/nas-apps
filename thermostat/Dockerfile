FROM node:22-alpine
WORKDIR /app
COPY server.js power.js ./
COPY public ./public
USER node
EXPOSE 8080
CMD ["node", "server.js"]
