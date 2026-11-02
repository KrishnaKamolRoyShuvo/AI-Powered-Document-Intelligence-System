FROM node:22-alpine
WORKDIR /app
# only install package versions that were published at least 14 days before this date
ARG NPM_BEFORE=2026-10-19
COPY package.json ./
RUN npm install --before=$NPM_BEFORE
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]
