# HeardOS dashboard — one small Node container: fetches Mealie, Actual Budget and Homebox over their APIs,
# caches the result for a few minutes and serves a single dark page + /api/summary.
FROM node:22-slim
ENV NODE_ENV=production TZ=America/Chicago
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --chown=node:node server.js ./
COPY --chown=node:node public ./public
# no recursive chown of node_modules: on overlayfs that copies up every file and takes minutes
RUN mkdir -p /data && chown node:node /data
USER node
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:8080/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
