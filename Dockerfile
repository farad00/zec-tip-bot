ARG NODE_IMAGE=node:24.22.1-bookworm-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
COPY test ./test
COPY deploy ./deploy
COPY Dockerfile .dockerignore ./
RUN npm run build
RUN node --test dist/test/oauth.test.js dist/test/bot.test.js dist/test/zcash.test.js dist/test/worker.test.js dist/test/web-render.test.js dist/test/cards.test.js dist/test/deployment.test.js dist/test/private-tips.test.js dist/test/wallet-probe.test.js dist/test/wallet-health.test.js dist/test/wallet-accounts.test.js dist/test/wallet-account-client.test.js dist/test/tip-engine-lease.test.js dist/test/wallet-transfers.test.js
RUN node --test dist/test/account-ui.test.js
RUN node --test dist/test/private-reply-context.test.js
RUN node --test dist/test/x-usage-guard.test.js
RUN node --test dist/test/web.test.js dist/test/public-site.test.js
RUN node --test dist/test/user-auth.test.js
RUN npm prune --omit=dev --ignore-scripts --no-audit --no-fund

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production WEB_HOST=0.0.0.0 WEB_PORT=3001 DATA_DIR=/app/data
WORKDIR /app
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/dist ./dist
COPY --chown=node:node package.json CARD-FONT-LICENSE.txt ./
COPY --chown=node:node scripts/check-deployment.mjs scripts/state-copy.mjs scripts/container-start.mjs scripts/wallet-probe.mjs ./scripts/
RUN mkdir -p /app/data && chown node:node /app/data
USER node
EXPOSE 3001
STOPSIGNAL SIGTERM
CMD ["node", "scripts/container-start.mjs"]

