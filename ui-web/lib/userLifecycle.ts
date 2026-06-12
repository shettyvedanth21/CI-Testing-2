import type { UserProfile } from "@/lib/authApi";

export type LifecycleAction = "resend_invite" | "reinvite" | "deactivate" | "reactivate";

export type LifecycleStatus = {
  label: "Active" | "Invited" | "Invite expired" | "Deactivated";
  variant: "success" | "info" | "warning" | "default";
};

export function getLifecycleStatus(user: UserProfile): LifecycleStatus {
  if (user.lifecycle_state === "invited") {
    return { label: "Invited", variant: "info" };
  }
  if (user.lifecycle_state === "invite_expired") {
    return { label: "Invite expired", variant: "warning" };
  }
  if (user.is_active || user.lifecycle_state === "active") {
    return { label: "Active", variant: "success" };
  }
  return { label: "Deactivated", variant: "default" };
}

export function getLifecycleActions(user: UserProfile): LifecycleAction[] {
  const actions: LifecycleAction[] = [];

  if (user.can_resend_invite) {
    actions.push(user.invite_status === "pending" ? "resend_invite" : "reinvite");
  }
  if (user.can_reactivate) {
    actions.push("reactivate");
  }
  if (user.can_deactivate !== false && user.is_active) {
    actions.push("deactivate");
  }

  return actions;
}
