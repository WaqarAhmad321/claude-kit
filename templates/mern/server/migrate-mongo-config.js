const url = process.env.MONGODB_URI;
if (!url) {
  throw new Error("Set MONGODB_URI to the database to migrate, then run the migrations again.");
}

export default {
  mongodb: { url },
  migrationsDir: "migrations",
  changelogCollectionName: "changelog",
  lockCollectionName: "changelog_lock",
  lockTtl: 0,
  migrationFileExtension: ".js",
  useFileHash: false,
  moduleSystem: "esm",
};
