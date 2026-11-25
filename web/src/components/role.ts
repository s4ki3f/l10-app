export const roleLabel = (role: string) =>
  ({ admin: "Admin", team_member: "Team Member", observer: "Observer" })[role] ?? role;
