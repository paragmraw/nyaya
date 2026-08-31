/** @type {import('next-sitemap').IConfig} */
// siteUrl mirrors SITE in web/src/lib/site.ts — keep in sync (this file is
// plain CJS run by npx, it cannot import the TS constant).
module.exports = {
  siteUrl: 'https://nyaya.parag.tech',
  generateRobotsTxt: true,
  generateIndexSitemap: true,
  outDir: 'out',
  exclude: ['/404', '/_not-found', '/_next/*', '/api/*', '/mcp', '/chat/*'],
  robotsTxtOptions: {
    policies: [{ userAgent: '*', allow: '/' }],
    additionalSitemaps: ['https://nyaya.parag.tech/sitemap.xml'],
  },
  transform: async (config, path) => {
    const trailingPath = path.endsWith('/') ? path : `${path}/`;
    return { loc: trailingPath };
  },
};