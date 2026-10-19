// Feature-owned catalogs, packaged locally.
import services from "./services.js";
import agents from "./agents.js";
import common from "./common.js";
import reviews from "./reviews.js";
import intake from "./intake.js";
import creation from "./creation.js";
import home from "./home.js";
import organization from "./organization.js";
import models from "./models.js";
import vaults from "./vaults.js";
import shell from "./shell.js";
export const messages = {
  ...services,
  ...agents,
  ...common,
  ...reviews,
  ...intake,
  ...creation,
  ...home,
  ...organization,
  ...models,
  ...vaults,
  ...shell,
};
