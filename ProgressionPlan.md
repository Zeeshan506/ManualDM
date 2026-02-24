Here is the parallel step-by-step backend integration plan for your FastAPI application, perfectly matching the frontend phases. This ensures your existing Meta API integrations and database logic remain intact while bolting on the new role-based architecture.

### PHASE 1: AUTHENTICATION AND FOUNDATION (Backend)

**Goal:** Establish user identity, protect endpoints, and define the database structures for users without altering the existing Lead ingestion flow.

* **File:** `models.py` (MODIFY)
* **Details:** Add a new `User` model. Fields: `id`, `username`, `hashed_password`, `role` (enum: 'admin', 'sales_rep'), and `is_active`.
* **Details:** Update the `Lead` model to include an `assigned_to` column (Foreign Key linking to `User.id`, nullable) and a `lead_status` column (defaulting to 'unassigned').


* **File:** `security.py` or `utils.py` (NEW/MODIFY)
* **Details:** Implement password hashing (e.g., using `passlib` with bcrypt) and JWT generation logic.


* **File:** `dependencies.py` (NEW)
* **Details:** Create reusable FastAPI dependencies: `get_current_user` (decodes JWT and fetches user) and `require_admin` (raises 403 if the user is not an admin).


* **File:** `routes/auth.py` (NEW)
* **Details:** Create a `POST /api/login` endpoint that verifies credentials and returns a JWT token. Create a `POST /api/password-reset-request` endpoint that logs a manager notification for sales reps.



### PHASE 2: NAVIGATION AND ROUTE GROUPS (Backend)

**Goal:** Provide the frontend with the necessary context to route users to the correct dashboard upon login.

* **File:** `routes/users.py` (NEW)
* **Details:** Create a `GET /api/users/me` endpoint. It uses the `get_current_user` dependency and returns the user's ID, username, and role. The frontend `layout` or `page.tsx` will call this to determine which dashboard to render.



### PHASE 3: THE SALES REP EXPERIENCE (Backend)

**Goal:** Power the "Unassigned Pool" and the "My Chats" view, ensuring data isolation.

* **File:** `routes/api.py` (MODIFY)
* **Details:** Update the existing `GET /api/leads` endpoint. Add optional query parameters: `status` and `assigned_to`.
* If `status=unassigned` is passed, query leads where `assigned_to IS NULL`.
* If `assigned_to={user_id}` is passed, return only that rep's active chats.




* **File:** `routes/api.py` (NEW ENDPOINT)
* **Details:** Create `PUT /api/leads/{lead_id}/assign`. This endpoint takes the current user's ID (from the token dependency), updates the lead's `assigned_to` field, and changes its status from 'unassigned' to 'active'.


* **File:** `services/event_handlers.py` (REVIEW/MODIFY)
* **Details:** Ensure that when new leads are automatically created via the Meta webhook, they are explicitly saved with `assigned_to = None` and `lead_status = 'unassigned'`.



### PHASE 4: THE ADMIN DASHBOARD (Backend)

**Goal:** Serve aggregated metrics and team tracking data for the admin views.

* **File:** `routes/admin.py` (NEW)
* **Details:** Create a `GET /api/admin/metrics` endpoint protected by the `require_admin` dependency. Use SQLAlchemy aggregation (`func.count`, `func.sum`) to calculate total revenue, total leads, and conversion rates from the `Lead` table.


* **File:** `routes/admin.py` (NEW)
* **Details:** Create a `GET /api/admin/team-activity` endpoint. Query the `User` table for all 'sales_rep' roles and join with the `Lead` table to return an array of objects containing the rep's ID, username, active chat count, and latest action timestamp.



### PHASE 5: CHAT INTERFACE CONTEXT ADAPTATION (Backend)

**Goal:** Enforce access control on the individual chat level and enable admin overrides.

* **File:** `routes/api.py` (MODIFY)
* **Details:** Secure the `GET /api/leads/{id}/messages` and `POST /api/leads/{id}/messages` endpoints. Add the `get_current_user` dependency.
* **Logic Check:** Before returning or posting messages, verify that `current_user.id == lead.assigned_to` OR `current_user.role == 'admin'`. Return a 403 Forbidden otherwise.


* **File:** `routes/admin.py` (NEW)
* **Details:** Create a `PUT /api/admin/leads/{lead_id}/reassign` endpoint. This allows admins to pass a new `user_id` in the payload to forcefully move a chat from one sales rep to another or push it back to the unassigned pool.