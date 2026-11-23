import graphScope from "./graph_scope.js";
import noteCloud from "./note_cloud.js";
// Feature-owned catalogs, packaged locally.
import services from "./services.js";
import agents from "./agents.js";
import common from "./common.js";
import reviews from "./reviews.js";
import intake from "./intake.js";
import creation from "./creation.js";
import home from "./home.js";
import organization from "./organization.js";
import organizationMembers from "./organization_members.js";
import organizationContinuity from "./organization_continuity.js";
import organizationRecovery from "./organization_recovery.js";
import organizationReliability from "./organization_reliability.js";
import organizationInboxReview from "./organization_inbox_review.js";
import organizationVisibility from "./organization_visibility.js";
import models from "./models.js";
import vaults from "./vaults.js";
import shell from "./shell.js";
export const messages = {
  ...graphScope,
  ...noteCloud,
  ...services,
  ...agents,
  ...common,
  ...reviews,
  ...intake,
  ...creation,
  ...home,
  ...organization,
  ...organizationMembers,
  ...organizationContinuity,
  ...organizationRecovery,
  ...organizationReliability,
  ...organizationVisibility,
  ...organizationInboxReview,
  ...models,
  ...vaults,
  ...shell,
};
