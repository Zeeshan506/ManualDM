Here is a unified, full-stack Progression Plan designed specifically for the next developer. It bridges the gap between the features I have already built and the steps required to scale the system for production.

You can copy and paste this directly into a new `ProgressionPlan.md` file in the root of the repository.

---

# PSC CRM: Full-Stack Progression Plan (V2 Roadmap)

This document outlines the step-by-step roadmap for future developers to scale the CRM. The foundation (Auth, WebSockets, Meta CAPI, Webhook ingestion, and basic routing) is complete. The following phases focus on finishing the Admin experience, migrating to live payments, and scaling the system for high traffic.

---

### PHASE 1: ADMIN OVERRIDES & READ-ONLY CHAT

**Goal:** Allow admins to monitor sales rep conversations safely and forcibly reassign leads without accidentally triggering outbound Instagram messages.

* **Backend (`app/api/routes/admin.py` & `api.py`)**
* Create a `PUT /api/admin/leads/{lead_id}/reassign` endpoint. This takes a payload of `{ "new_user_id": int | null }`. It forces the lead's `assigned_to` field to update, sets `lead_status` accordingly, and logs an `ActivityEvent`.
* Update `GET /api/leads/{id}/messages` to ensure that if the requester is an Admin, they can read the messages even if they don't own the lead.


* **Frontend (`app/leads/[id]/page.tsx` & Components)**
* Read the current user's role from `AuthContext` and pass an `isAdmin` boolean prop down to child components.
* Modify `MessageInput.tsx`: If `isAdmin` is true, disable the text input and replace the send button with a warning label: *"Read-Only: Viewing as Admin"*.
* Modify `RightDetailsPanel.tsx`: If `isAdmin` is true, render a "Reassign Chat" dropdown module that hits the new backend reassignment endpoint.



### PHASE 2: TEAM ACTIVITY & ANALYTICS WIRING

**Goal:** Bring the Admin tracking dashboards to life with real, aggregated database metrics.

* **Backend (`app/api/routes/admin.py`)**
* Build `GET /api/admin/team-activity`. Use SQLAlchemy to join the `User` table (where role is `sales_rep`) with the `Lead` and `Message` tables.
* Return an array of objects containing: `rep_id`, `username`, `active_chat_count`, `leads_claimed_today`, and `conversion_rate`.


* **Frontend (`app/(admin)/team-activity/page.tsx`)**
* Connect the existing `RepPerformanceTable` and `ActivityLogTable` components to the new endpoints using TanStack React Query.
* Ensure the data refreshes periodically (e.g., every 30 seconds) so admins have a live view of the sales floor.



### PHASE 3: LIVE STRIPE PAYMENTS INTEGRATION

**Goal:** Transition the platform from using manual "Mock Purchases" to processing real transactions and automatically firing Meta CAPI events upon payment success.

* **Backend (`app/api/routes/api.py` & `app/services/`)**
* Integrate the `stripe` Python SDK. Replace the manual invoice creation logic with an endpoint that generates a Stripe Checkout Session URL.
* Create a dedicated `POST /api/webhooks/stripe` endpoint.
* Listen for the `checkout.session.completed` event. When triggered, locate the associated `Lead`, mark the invoice as paid, and enqueue the Celery task (`tasks.post_meta_conversion_event`) to fire the `Purchase` event to Meta.


* **Frontend (`app/leads/[id]/components/RightDetailsPanel.tsx`)**
* Replace the "Create Mock Purchase" flow. Add a "Generate Payment Link" button that fetches the Stripe Checkout URL from the backend and drops it into the chat input for the Sales Rep to send to the Instagram user.



### PHASE 4: AUTOMATION & SCALING (Technical Debt)

**Goal:** Prepare the database and UI to handle thousands of concurrent conversations without browser lag or database deadlocks.

* **Backend (Lead Routing & Optimization)**
* **Automated Routing:** Update `services/webhook_events.py`. Instead of dropping new leads into the Unassigned Pool, implement a Round-Robin Celery task. Query online sales reps and auto-assign the lead to the rep with the lowest `active_chat_count`.
* **Pagination:** Update `GET /api/leads/{lead_id}/messages` to accept `limit` and `cursor` parameters.
* **Database Indexing:** Create an Alembic migration to add indexes to frequently queried columns: `leads.assigned_to`, `contacts.igsid`, and `webhook_events.idempotency_key`.


* **Frontend (Infinite Scroll)**
* Update `MessagesArea.tsx` to use TanStack Query's `useInfiniteQuery`.
* Implement an intersection observer so that scrolling to the top of the chat window fetches the previous page of messages, preventing the browser from overloading the DOM with thousands of nodes at once.



### PHASE 5: RELIABILITY & CI/CD

**Goal:** Protect the core Instagram integration from regressions during future feature updates.

* **Backend (Pytest)**
* Write isolated unit tests for `services/webhook_events.py` to ensure duplicate `mid` payloads from Meta do not create duplicate messages or crash the parser.
* Write tests for `services/meta_conversion_events.py` to guarantee that emails and phone numbers are always correctly normalized and SHA-256 hashed before being sent to the Meta Graph API.


* **Frontend (Jest / React Testing Library)**
* Write tests for the `AuthContext` to ensure unauthorized users are strictly booted from `/admin` route groups.