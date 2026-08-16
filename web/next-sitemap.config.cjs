/** @type {import('next-sitemap').IConfig} */
module.exports = {
  siteUrl: 'https://nyaya.example.com',
  generateRobotsTxt: true,
  generateIndexSitemap: true,
  outDir: 'out',
  exclude: ['/404', '/_not-found', '/_next/*', '/api/*', '/mcp', '/chat/*'],
  robotsTxtOptions: {
    policies: [{ userAgent: '*', allow: '/' }],
    additionalSitemaps: ['https://nyaya.example.com/sitemap.xml'],
  },
  transform: async (config, path) => {
    const trailingPath = path.endsWith('/') ? path : `${path}/`;
    return { loc: trailingPath };
  },
};