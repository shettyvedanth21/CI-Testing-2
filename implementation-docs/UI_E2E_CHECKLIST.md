# UI E2E Master Checklist (Playwright)

Source scope: `/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/ui-web`  
Assessment basis: existing Playwright specs under `ui-web/tests/e2e` + current UI routes/components.  
Status legend: `[C]` Completed in current Playwright coverage, `[P]` Partial coverage, `[M]` Missing coverage.

## Summary

- Total checklist items: **88**
- Completed: **88**
- Partial: **0**
- Missing: **0**
- Current major gap: none in the deterministic UI contract slice. The remaining validation tradeoff is that some enterprise flows are still route-mocked for deterministic browser coverage rather than backed by live services.
- Phase 1 status: default deterministic Playwright slice is now green in `headless`, `headed`, and Playwright UI mode; live/local/preprod validations are explicitly opt-in so one-command local validation stays reliable.

## 1) Auth And Session

1. [C] Login form rejects empty email/password and shows validation message.
2. [C] Login with invalid credentials shows truthful backend error.
3. [C] Login success redirects to `/machines`.
4. [C] Access token refresh runs before protected API retry.
5. [C] Expired refresh token returns user to `/login`.
6. [C] Forgot-password request UI flow validates success and failure messages.
7. [C] Reset-password page validates mismatch password guard.
8. [C] Accept-invite page validates valid token path and invalid token path.
9. [C] Logout clears session and denies protected route access.

## 2) Tenant / Org / Role Bootstrapping

10. [C] Super admin creates organization from UI and sees org in admin table.
11. [C] Super admin creates org admin invite from UI.
12. [C] Invited org admin accepts invite and can sign in.
13. [C] Org admin can view only own tenant users/plants.
14. [C] Role-scoped visibility differs across org admin/plant manager/operator/viewer.
15. [C] Role-scoped navigation hides unauthorized modules.
16. [C] Role update in UI immediately changes module visibility after re-login.
17. [C] Deactivated user is denied UI login and sees truthful error.

## 3) Plant Lifecycle

18. [C] Plant list page loads assigned plants with truthful empty state.
19. [C] Create plant form validates required fields and saves.
20. [C] Edit plant updates persisted data and reflects in selector labels.
21. [C] Deactivate plant blocks new onboarding to that plant.
22. [C] Reactivate plant restores onboarding eligibility.
23. [C] Duplicate plant name errors are surfaced clearly, and plant slug/path handling is truthfully derived from the saved plant name rather than exposed as a separate editable product field.

## 4) Device Onboarding And Provisioning

24. [C] Onboarding blocks when org has no plants.
25. [C] Onboarding generates and displays created device ID.
26. [C] Onboarding form validates mandatory fields before submit.
27. [C] Onboarding handles conflict response with truthful message.
28. [C] Onboarding failure due to backend ID-allocation failure shows stable error contract.
29. [C] Onboarded device appears in machine list without manual refresh.
30. [C] Device detail hides MQTT secrets after onboarding.
31. [C] MQTT rotate action updates credential state in UI.
32. [C] Revoke credential state is shown and previously issued credentials become visibly invalid in UI.

## 5) Machines Dashboard And Runtime

33. [C] Empty-tenant machines page remains stable (no reconnect flicker).
34. [C] Machines page reconnects after stream disruption.
35. [C] Auth refresh occurs before reconnect attempt.
36. [C] Fleet summary tiles show truthful zero-state for no devices.
37. [C] Recent telemetry pagination behaves consistently.
38. [C] Stale bootstrap fallback recovers machine detail page.
39. [C] Running/stopped/unknown counts contract is validated against API payload classes.
40. [C] Dashboard loss/cost cards show truthful unavailable/stale/fresh state transitions.

## 6) Machine Detail, Health, Parameters, Efficiency

41. [C] Machine detail read-only tabs enforced for viewer role.
42. [C] Parameter configuration tab visibility is role-gated.
43. [C] Health-config create form rejects non-finite and inverted ranges while preserving the truthful empty-state contract.
44. [C] Health-config update recomputes score card with visible new value.
45. [C] Efficiency score widget reflects backend-calculated score state.
46. [C] Formula/weight edit history reflects the latest saved configuration in the machine detail history panel.
47. [C] Missing health config returns the truthful `Not configured` empty state in machine detail.

## 7) Shift, Uptime, Calendar

48. [C] Shift screen opens and renders existing shift data.
49. [C] Create shift validates overlap rejection with clear message.
50. [C] Edit shift boundary changes reflect immediately in uptime card.
51. [C] Delete shift handles missing-shift response truthfully.
52. [C] Uptime card shows “no active shifts” contract correctly.
53. [C] Calendar page loads monthly data and navigation controls.
54. [C] Month rollover handles snapshot unavailable fallback truthfully.
55. [C] Plant-scoped calendar summary reflects the selected plant filter.

## 8) Rules And Alerts

56. [C] Rules navigation and scope UX by role is enforced.
57. [C] Create rule form for idle duration path is functional.
58. [C] Create each rule type variant with field-level validation.
59. [C] Edit rule persists changes and reflects in list/detail.
60. [C] Delete rule soft-delete UX and list removal contract.
61. [C] Out-of-scope rule mutation attempt is blocked truthfully.
62. [C] Triggered rule produces alert badge/notification visibility in UI.
63. [C] Supported alert status actions update status in UI truthfully, including acknowledge and resolve flows surfaced from machine alert history.

## 9) Maintenance Logs

64. [C] Maintenance modal is reachable from machine page.
65. [C] Add/edit/delete maintenance flow is deterministic in the default Playwright slice.
66. [C] Viewer cannot mutate maintenance records.
67. [C] Zero-record maintenance summary shows truthful empty state.
68. [C] Missing maintenance record delete returns stable UI error contract.

## 10) Analytics, Reports, Waste, Copilot

69. [C] Premium module nav hidden when entitlements missing.
70. [C] Premium module nav visible when entitlements granted.
71. [C] Analytics page handles no-access state and basic load path.
72. [C] Analytics job submit → status → result journey verified.
73. [C] Analytics failed job status is surfaced truthfully in UI.
74. [C] Reports generate/download happy path verified end-to-end.
75. [C] Reports no-data/not-ready contracts surfaced truthfully.
76. [C] Scheduled report create/edit/disable lifecycle is covered truthfully.
77. [C] Waste analysis run and result presentation verified.
78. [C] Copilot fallback states (`AI_UNAVAILABLE`, internal error) verified with real calls.

## 11) Tariff, Cost, Energy Formula

79. [C] Tariff create form validates negative and non-numeric values and persists a valid save contract.
80. [C] Tariff version boundary selection reflects the expected active tariff.
81. [C] Tariff changes propagate to cost widgets on dashboard.
82. [C] Energy formula/hidden insight presentation matches backend state.
83. [C] Currency and unit formatting contract stays stable across pages.

## 12) Platform Maintenance And Admin Hardware

84. [C] Platform maintenance UI handles selected-org targeting and exclusion messaging.
85. [C] Tenant banner shows active/scheduled notice states.
86. [C] Admin hardware inventory management UI flow covered.
87. [C] Platform maintenance overlap rejection path verified in UI.
88. [C] Platform maintenance delete/update missing-record truthfulness in UI.

## Missed Gap Callouts

- Gap A: Closed for the default deterministic slice by `journey-happy-path.spec.js`, which now chains login → onboarding → config → shift/uptime → rules → alerts → tariff/report/calendar → dashboard truthfulness.
- Gap B: Several specs still rely on **route-mocked API responses**. That gives strong UI-contract coverage, but it is still weaker than full live-service integration proof.
- Gap C: The default deterministic browser slice is broad and stable and now closes the full checklist.
- Gap D: The remaining caveat is service realism, not browser coverage: several enterprise paths are stabilized through exact route-mocked contracts so one-command local validation remains deterministic.

## Next Execution Order (Recommended)

1. Keep the default deterministic suite green as the standing browser regression gate.
2. Expand route-mocked coverage into live-service-backed validations only where stable CI infrastructure exists.
3. Preserve the current product/UI contracts so the 88-row checklist stays truthful over time.
