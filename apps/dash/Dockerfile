# HeardOS desktop — one small Node container: the web desktop (public/), the app catalogue (apps.json, filtered per person
# from the Authentik gate's headers) and /api/summary (Mealie, Actual Budget, Homebox) for the This week app and widgets.
FROM node:22-slim
ENV NODE_ENV=production TZ=America/Chicago
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --chown=node:node server.js apps.json fetch-icons.mjs ./
COPY --chown=node:node public ./public
RUN node fetch-icons.mjs && chown -R node:node public/icons
# no recursive chown of node_modules: on overlayfs that copies up every file and takes minutes
RUN mkdir -p /data && chown node:node /data
USER node
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:8080/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
