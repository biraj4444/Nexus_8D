import sys
import os

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    session,
    flash,
    abort,
)
from functools import wraps
from datetime import datetime

from common.db import get_db
from common.config import Config
from common.cloudinary_utils import (
    upload_to_cloudinary,
    delete_from_cloudinary,
)


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

    # ── Auth ──────────────────────────────────────────────────────────────

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("admin_logged_in"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)

        return decorated

    @app.context_processor
    def inject_globals():
        db = get_db()

        pending = (
            db.count("items", {"is_approved": False})
            if session.get("admin_logged_in")
            else 0
        )

        pending_suggestions = (
            db.count("suggestions", {"status": "pending"})
            if session.get("admin_logged_in")
            else 0
        )

        return dict(
            site_name="NexusVault Admin",
            year=datetime.now().year,
            pending_count=pending,
            pending_suggestions_count=pending_suggestions,
            admin_user=session.get("admin_user", ""),
        )

    # ── Login ─────────────────────────────────────────────────────────────

    @app.route("/", methods=["GET", "POST"])
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("admin_logged_in"):
            return redirect(url_for("dashboard"))

        error = None

        if request.method == "POST":
            u = request.form.get("username", "").strip()
            p = request.form.get("password", "")

            if (
                u == Config.ADMIN_USERNAME
                and p == Config.ADMIN_PASSWORD
            ):
                session["admin_logged_in"] = True
                session["admin_user"] = u

                return redirect(url_for("dashboard"))

            error = "Invalid credentials."

        return render_template(
            "login.html",
            error=error,
        )

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ── Dashboard ────────────────────────────────────────────────────────

    @app.route("/dashboard")
    @login_required
    def dashboard():
        db = get_db()

        stats = {
            "total": db.count("items", {}),
            "approved": db.count(
                "items",
                {"is_approved": True},
            ),
            "pending": db.count(
                "items",
                {"is_approved": False},
            ),
            "links": db.count(
                "items",
                {"type": "link"},
            ),
            "videos": db.count(
                "items",
                {"type": "video"},
            ),
            "images": db.count(
                "items",
                {"type": "image"},
            ),
            "files": db.count(
                "items",
                {"type": "file"},
            ),
            "categories": db.count(
                "categories",
                {},
            ),
            "zones": db.count(
                "zones",
                {},
            ),
            "discussions": db.count(
                "discussions",
                {},
            ),
        }

        recent = db.find(
            "items",
            {},
            limit=10,
        )

        pending = db.find(
            "items",
            {"is_approved": False},
            limit=8,
        )

        return render_template(
            "dashboard.html",
            stats=stats,
            recent=recent,
            pending=pending,
            active="dashboard",
        )

    # ── Manage items ─────────────────────────────────────────────────────

    @app.route("/items")
    @login_required
    def items():
        db = get_db()

        page = request.args.get(
            "page",
            1,
            type=int,
        )

        per_page = 20
        skip = (page - 1) * per_page

        itype = request.args.get("type")
        source = request.args.get("source")
        approved = request.args.get("approved")
        cat_slug = request.args.get("category")
        q_str = request.args.get(
            "q",
            "",
        ).strip()

        filters = {}

        if itype:
            filters["type"] = itype

        if source:
            filters["source_type"] = source

        if cat_slug:
            filters["category_slug"] = cat_slug

        if approved == "1":
            filters["is_approved"] = True

        elif approved == "0":
            filters["is_approved"] = False

        if q_str:
            all_items = db.search(
                "items",
                q_str,
                filters,
            )

            total = len(all_items)

            all_items = all_items[
                skip: skip + per_page
            ]

        else:
            total = db.count(
                "items",
                filters,
            )

            all_items = db.find(
                "items",
                filters,
                skip=skip,
                limit=per_page,
            )

        total_pages = max(
            1,
            (total + per_page - 1) // per_page,
        )

        cats = db.find(
            "categories",
            {},
            sort=[("sort_order", 1)],
        )

        return render_template(
            "items.html",
            items=all_items,
            page=page,
            total_pages=total_pages,
            total=total,
            itype=itype,
            source=source,
            approved=approved,
            cat_slug=cat_slug,
            q_str=q_str,
            categories=cats,
            active="items",
        )

    # ── Insert item ──────────────────────────────────────────────────────

    @app.route("/insert", methods=["GET", "POST"])
    @login_required
    def insert():
        db = get_db()

        cats = db.find(
            "categories",
            {},
            sort=[("sort_order", 1)],
        )

        zones = db.find(
            "zones",
            {},
            sort=[("sort_order", 1)],
        )

        error = None
        success_id = None

        if request.method == "POST":
            itype = request.form.get(
                "type",
                "link",
            ).strip().lower()

            title = request.form.get(
                "title",
                "",
            ).strip()

            desc = request.form.get(
                "description",
                "",
            ).strip()

            cat_slug = request.form.get(
                "category_slug",
                "official",
            ).strip()

            z_slug = request.form.get(
                "zone_slug",
                "global",
            ).strip()

            tags = [
                t.strip()
                for t in request.form.get(
                    "tags",
                    "",
                ).split(",")
                if t.strip()
            ]

            featured = bool(
                request.form.get("is_featured")
            )

            uploader = (
                request.form.get(
                    "uploader_name",
                    "admin",
                ).strip()
                or "admin"
            )

            extra = {}

            for k in (
                "author",
                "source",
                "language",
                "format",
                "size",
                "duration",
            ):
                v = request.form.get(
                    f"extra_{k}",
                    "",
                ).strip()

                if v:
                    extra[k] = v

            # Validate content type
            if itype not in (
                "link",
                "video",
                "image",
                "file",
            ):
                error = "Invalid content type."

            # Validate title
            elif not title:
                error = "Title is required."

            else:
                item = dict(
                    title=title,
                    description=desc,
                    type=itype,
                    source_type="admin",
                    category_slug=cat_slug,
                    zone_slug=z_slug,
                    tags=tags,
                    uploader_name=uploader,
                    is_approved=True,
                    is_featured=featured,
                    views=0,
                    downloads=0,
                    url="",
                    thumbnail_url="",
                    cloudinary_id="",
                    cloudinary_url="",
                    extra_details=extra,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )

                # ── Link ────────────────────────────────────────────────

                if itype == "link":
                    item["url"] = request.form.get(
                        "url",
                        "",
                    ).strip()

                    item["thumbnail_url"] = request.form.get(
                        "thumbnail_url",
                        "",
                    ).strip()

                    if not item["url"]:
                        error = "URL is required."

                # ── Video / Image / File ─────────────────────────────────

                else:
                    # IMPORTANT:
                    # insert.html contains multiple file inputs with
                    # name="file". get("file") can return an empty one.
                    # getlist() lets us find the actual selected file.
                    uploaded_files = request.files.getlist(
                        "file"
                    )

                    file = next(
                        (
                            f
                            for f in uploaded_files
                            if f is not None
                            and getattr(f, "filename", "")
                        ),
                        None,
                    )

                    ext_url = request.form.get(
                        "external_url",
                        "",
                    ).strip()

                    # ── Actual file upload ──────────────────────────────

                    if file is not None:
                        try:
                            res = upload_to_cloudinary(
                                file,
                                itype,
                            )

                        except Exception as exc:
                            print(
                                f"[Admin] Cloudinary upload exception: {exc}"
                            )
                            res = None

                        if res:
                            item.update(
                                cloudinary_id=res.get(
                                    "public_id",
                                    "",
                                ),
                                cloudinary_url=res.get(
                                    "url",
                                    "",
                                ),
                                url=res.get(
                                    "url",
                                    "",
                                ),
                                thumbnail_url=res.get(
                                    "thumbnail_url",
                                    res.get(
                                        "url",
                                        "",
                                    ),
                                ),
                            )

                            if res.get("duration"):
                                extra["duration"] = str(
                                    res["duration"]
                                )

                            if res.get("format"):
                                extra["format"] = str(
                                    res["format"]
                                )

                        else:
                            error = (
                                "Cloudinary upload failed."
                            )

                    # ── External URL ────────────────────────────────────

                    elif ext_url:
                        item["url"] = ext_url

                        item["thumbnail_url"] = request.form.get(
                            "thumbnail_url",
                            "",
                        ).strip()

                    # ── Nothing provided ─────────────────────────────────

                    else:
                        error = "Provide a file or URL."

                # ── Save item ───────────────────────────────────────────

                if not error:
                    success_id = db.insert(
                        "items",
                        item,
                    )

                    flash(
                        "Item added successfully!",
                        "success",
                    )

        return render_template(
            "insert.html",
            categories=cats,
            zones=zones,
            error=error,
            success_id=success_id,
            active="insert",
        )

    # ── Edit item ────────────────────────────────────────────────────────

    @app.route(
        "/item/<item_id>/edit",
        methods=["GET", "POST"],
    )
    @login_required
    def edit_item(item_id):
        db = get_db()

        item = db.find_one(
            "items",
            {"_id": item_id},
        )

        if not item:
            abort(404)

        cats = db.find(
            "categories",
            {},
            sort=[("sort_order", 1)],
        )

        zones = db.find(
            "zones",
            {},
            sort=[("sort_order", 1)],
        )

        error = None

        if request.method == "POST":
            tags = [
                t.strip()
                for t in request.form.get(
                    "tags",
                    "",
                ).split(",")
                if t.strip()
            ]

            extra = item.get(
                "extra_details",
                {},
            )

            # Make a copy so we don't unexpectedly modify
            # the object returned from the database.
            extra = dict(extra)

            for k in (
                "author",
                "source",
                "language",
                "format",
                "size",
                "duration",
            ):
                v = request.form.get(
                    f"extra_{k}",
                    "",
                ).strip()

                if v:
                    extra[k] = v

            updates = dict(
                title=request.form.get(
                    "title",
                    "",
                ).strip(),

                description=request.form.get(
                    "description",
                    "",
                ).strip(),

                category_slug=request.form.get(
                    "category_slug",
                    "",
                ).strip(),

                zone_slug=request.form.get(
                    "zone_slug",
                    "",
                ).strip(),

                tags=tags,

                is_featured=bool(
                    request.form.get(
                        "is_featured"
                    )
                ),

                is_approved=bool(
                    request.form.get(
                        "is_approved"
                    )
                ),

                uploader_name=request.form.get(
                    "uploader_name",
                    "",
                ).strip(),

                url=request.form.get(
                    "url",
                    "",
                ).strip(),

                thumbnail_url=request.form.get(
                    "thumbnail_url",
                    "",
                ).strip(),

                extra_details=extra,

                updated_at=datetime.utcnow(),
            )

            if not updates["title"]:
                error = "Title is required."

            else:
                # ── Handle new file replacement ─────────────────────────

                uploaded_files = request.files.getlist(
                    "file"
                )

                new_file = next(
                    (
                        f
                        for f in uploaded_files
                        if f is not None
                        and getattr(f, "filename", "")
                    ),
                    None,
                )

                if new_file is not None:
                    # Delete previous Cloudinary resource first.
                    if item.get("cloudinary_id"):
                        try:
                            delete_from_cloudinary(
                                item["cloudinary_id"],
                                item.get(
                                    "type",
                                    "raw",
                                ),
                            )
                        except Exception as exc:
                            print(
                                f"[Admin] Cloudinary delete exception: {exc}"
                            )

                    try:
                        res = upload_to_cloudinary(
                            new_file,
                            item.get(
                                "type",
                                "file",
                            ),
                        )

                    except Exception as exc:
                        print(
                            f"[Admin] Cloudinary replacement exception: {exc}"
                        )
                        res = None

                    if res:
                        updates.update(
                            cloudinary_id=res.get(
                                "public_id",
                                "",
                            ),
                            cloudinary_url=res.get(
                                "url",
                                "",
                            ),
                            url=res.get(
                                "url",
                                "",
                            ),
                            thumbnail_url=res.get(
                                "thumbnail_url",
                                res.get(
                                    "url",
                                    "",
                                ),
                            ),
                        )

                    else:
                        error = "Upload failed."

                # ── Save changes ────────────────────────────────────────

                if not error:
                    db.update(
                        "items",
                        {"_id": item_id},
                        {"$set": updates},
                    )

                    flash(
                        "Item updated.",
                        "success",
                    )

                    return redirect(
                        url_for(
                            "edit_item",
                            item_id=item_id,
                        )
                    )

        item = db.find_one(
            "items",
            {"_id": item_id},
        )

        return render_template(
            "edit_item.html",
            item=item,
            categories=cats,
            zones=zones,
            error=error,
            active="items",
        )

    # ── Delete item ──────────────────────────────────────────────────────

    @app.route(
        "/item/<item_id>/delete",
        methods=["POST"],
    )
    @login_required
    def delete_item(item_id):
        db = get_db()

        item = db.find_one(
            "items",
            {"_id": item_id},
        )

        if item and item.get("cloudinary_id"):
            try:
                delete_from_cloudinary(
                    item["cloudinary_id"],
                    item.get(
                        "type",
                        "raw",
                    ),
                )
            except Exception as exc:
                print(
                    f"[Admin] Cloudinary delete exception: {exc}"
                )

        db.delete(
            "items",
            {"_id": item_id},
        )

        db.delete(
            "discussions",
            {"item_id": item_id},
        )

        flash(
            "Item deleted.",
            "success",
        )

        return redirect(
            url_for("items")
        )

    # ── Approve / feature toggle ─────────────────────────────────────────

    @app.route(
        "/item/<item_id>/approve",
        methods=["POST"],
    )
    @login_required
    def approve_item(item_id):
        db = get_db()

        item = db.find_one(
            "items",
            {"_id": item_id},
        )

        if not item:
            abort(404)

        new_val = not item.get(
            "is_approved",
            False,
        )

        db.update(
            "items",
            {"_id": item_id},
            {
                "$set": {
                    "is_approved": new_val,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        return jsonify(
            {
                "ok": True,
                "is_approved": new_val,
            }
        )

    @app.route(
        "/item/<item_id>/feature",
        methods=["POST"],
    )
    @login_required
    def feature_item(item_id):
        db = get_db()

        item = db.find_one(
            "items",
            {"_id": item_id},
        )

        if not item:
            abort(404)

        new_val = not item.get(
            "is_featured",
            False,
        )

        db.update(
            "items",
            {"_id": item_id},
            {
                "$set": {
                    "is_featured": new_val,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        return jsonify(
            {
                "ok": True,
                "is_featured": new_val,
            }
        )

    # ── Categories ───────────────────────────────────────────────────────

    @app.route(
        "/categories",
        methods=["GET", "POST"],
    )
    @login_required
    def categories():
        db = get_db()
        error = None

        if request.method == "POST":
            action = request.form.get(
                "action",
                "create",
            )

            if action == "create":
                name = request.form.get(
                    "name",
                    "",
                ).strip()

                slug = (
                    request.form.get(
                        "slug",
                        "",
                    )
                    .strip()
                    .lower()
                    .replace(" ", "-")
                )

                icon = request.form.get(
                    "icon",
                    "📁",
                ).strip()

                color = request.form.get(
                    "color",
                    "#555",
                ).strip()

                desc = request.form.get(
                    "description",
                    "",
                ).strip()

                if not name or not slug:
                    error = "Name and slug are required."

                elif db.find_one(
                    "categories",
                    {"slug": slug},
                ):
                    error = (
                        f"Slug '{slug}' already exists."
                    )

                else:
                    db.insert(
                        "categories",
                        {
                            "name": name,
                            "slug": slug,
                            "icon": icon,
                            "color": color,
                            "description": desc,
                            "sort_order": (
                                db.count(
                                    "categories",
                                    {},
                                )
                                + 1
                            ),
                            "created_at": datetime.utcnow(),
                        },
                    )

                    flash(
                        f"Category '{name}' created.",
                        "success",
                    )

                    return redirect(
                        url_for("categories")
                    )

            elif action == "delete":
                cat_id = request.form.get(
                    "cat_id",
                    "",
                )

                db.delete(
                    "categories",
                    {"_id": cat_id},
                )

                flash(
                    "Category deleted.",
                    "success",
                )

                return redirect(
                    url_for("categories")
                )

        cats = db.find(
            "categories",
            {},
            sort=[("sort_order", 1)],
        )

        counts = {
            c["slug"]: db.count(
                "items",
                {"category_slug": c["slug"]},
            )
            for c in cats
        }

        return render_template(
            "categories.html",
            categories=cats,
            counts=counts,
            error=error,
            active="categories",
        )

    # ── Zones ────────────────────────────────────────────────────────────

    @app.route(
        "/zones",
        methods=["GET", "POST"],
    )
    @login_required
    def zones():
        db = get_db()
        error = None

        if request.method == "POST":
            action = request.form.get(
                "action",
                "create",
            )

            if action == "create":
                name = request.form.get(
                    "name",
                    "",
                ).strip()

                slug = (
                    request.form.get(
                        "slug",
                        "",
                    )
                    .strip()
                    .lower()
                    .replace(" ", "-")
                )

                country = request.form.get(
                    "country",
                    "",
                ).strip()

                parent = (
                    request.form.get(
                        "parent_slug",
                        "",
                    ).strip()
                    or None
                )

                flag = request.form.get(
                    "flag_emoji",
                    "🌐",
                ).strip()

                desc = request.form.get(
                    "description",
                    "",
                ).strip()

                if not name or not slug:
                    error = "Name and slug are required."

                elif db.find_one(
                    "zones",
                    {"slug": slug},
                ):
                    error = (
                        f"Slug '{slug}' already exists."
                    )

                else:
                    db.insert(
                        "zones",
                        {
                            "name": name,
                            "slug": slug,
                            "country": country,
                            "parent_slug": parent,
                            "flag_emoji": flag,
                            "description": desc,
                            "sort_order": (
                                db.count(
                                    "zones",
                                    {},
                                )
                                + 1
                            ),
                            "created_at": datetime.utcnow(),
                        },
                    )

                    flash(
                        f"Zone '{name}' created.",
                        "success",
                    )

                    return redirect(
                        url_for("zones")
                    )

            elif action == "delete":
                zone_id = request.form.get(
                    "zone_id",
                    "",
                )

                db.delete(
                    "zones",
                    {"_id": zone_id},
                )

                flash(
                    "Zone deleted.",
                    "success",
                )

                return redirect(
                    url_for("zones")
                )

        all_zones = db.find(
            "zones",
            {},
            sort=[("sort_order", 1)],
        )

        counts = {
            z["slug"]: db.count(
                "items",
                {"zone_slug": z["slug"]},
            )
            for z in all_zones
        }

        return render_template(
            "zones.html",
            zones=all_zones,
            counts=counts,
            active="zones",
        )

    # ── Discussions ──────────────────────────────────────────────────────

    @app.route("/discussions")
    @login_required
    def discussions():
        db = get_db()

        page = request.args.get(
            "page",
            1,
            type=int,
        )

        per_page = 30
        skip = (page - 1) * per_page

        all_discs = db.find(
            "discussions",
            {},
            skip=skip,
            limit=per_page,
        )

        total = db.count(
            "discussions",
            {},
        )

        total_pages = max(
            1,
            (total + per_page - 1) // per_page,
        )

        return render_template(
            "discussions.html",
            discussions=all_discs,
            page=page,
            total_pages=total_pages,
            total=total,
            active="discussions",
        )

    @app.route(
        "/discussion/<disc_id>/delete",
        methods=["POST"],
    )
    @login_required
    def delete_discussion(disc_id):
        db = get_db()

        db.delete(
            "discussions",
            {"_id": disc_id},
        )

        db.delete(
            "discussions",
            {"parent_id": disc_id},
        )

        flash(
            "Discussion deleted.",
            "success",
        )

        return redirect(
            url_for("discussions")
        )

    # ── Suggestions review ───────────────────────────────────────────────

    @app.route("/suggestions")
    @login_required
    def suggestions():
        db = get_db()

        sugs = db.find(
            "suggestions",
            {},
        )

        return render_template(
            "suggestions.html",
            suggestions=sugs,
            active="suggestions",
        )

    @app.route(
        "/suggestion/<sug_id>/approve",
        methods=["POST"],
    )
    @login_required
    def approve_suggestion(sug_id):
        db = get_db()

        sug = db.find_one(
            "suggestions",
            {"_id": sug_id},
        )

        if sug:
            db.insert(
                "items",
                {
                    "title": sug["name"],
                    "description": sug.get("description", ""),
                    "type": "link",
                    "source_type": "admin",
                    "category_slug": sug.get("category_slug", "public"),
                    "zone_slug": "global",
                    "tags": [],
                    "uploader_name": "admin",
                    "is_approved": True,
                    "is_featured": False,
                    "views": 0,
                    "downloads": 0,
                    "url": sug.get("url", ""),
                    "thumbnail_url": "",
                    "cloudinary_id": "",
                    "cloudinary_url": "",
                    "extra_details": {},
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
            )

            db.update(
                "suggestions",
                {"_id": sug_id},
                {"$set": {"status": "approved"}},
            )

            flash(
                "Suggestion approved and added.",
                "success",
            )

        return redirect(
            url_for("suggestions")
        )

    @app.route(
        "/suggestion/<sug_id>/reject",
        methods=["POST"],
    )
    @login_required
    def reject_suggestion(sug_id):
        get_db().update(
            "suggestions",
            {"_id": sug_id},
            {"$set": {"status": "rejected"}},
        )

        flash(
            "Suggestion rejected.",
            "info",
        )

        return redirect(
            url_for("suggestions")
        )

    # ── Registered users ─────────────────────────────────────────────────

    @app.route("/users")
    @login_required
    def users():
        db = get_db()

        all_users = db.find(
            "users",
            {},
        )

        return render_template(
            "users.html",
            users=all_users,
            active="users",
        )

    # ── API: live dashboard stats ────────────────────────────────────────

    @app.route("/api/stats")
    @login_required
    def api_stats():
        db = get_db()

        return jsonify(
            {
                "total": db.count(
                    "items",
                    {},
                ),
                "approved": db.count(
                    "items",
                    {"is_approved": True},
                ),
                "pending": db.count(
                    "items",
                    {"is_approved": False},
                ),
                "links": db.count(
                    "items",
                    {"type": "link"},
                ),
                "videos": db.count(
                    "items",
                    {"type": "video"},
                ),
                "images": db.count(
                    "items",
                    {"type": "image"},
                ),
                "files": db.count(
                    "items",
                    {"type": "file"},
                ),
                "discussions": db.count(
                    "discussions",
                    {},
                ),
                "categories": db.count(
                    "categories",
                    {},
                ),
                "zones": db.count(
                    "zones",
                    {},
                ),
            }
        )

    # ── Error handlers ───────────────────────────────────────────────────

    @app.errorhandler(404)
    def not_found(e):
        return render_template(
            "404.html"
        ), 404

    @app.route("/test-cloudinary-upload", methods=["GET"])
    def test_cloudinary_upload():
        from io import BytesIO
        from types import SimpleNamespace
        from common.cloudinary_utils import upload_to_cloudinary

        # Tiny valid 1x1 PNG
        png_data = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
            b"\x05\x00\x01\xff\x89\x99=\x1d"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        file_obj = BytesIO(png_data)
        file_obj.filename = "cloudinary-test.png"

        try:
            result = upload_to_cloudinary(file_obj, "image")

            if result:
                return jsonify({
                    "success": True,
                    "message": "REAL CLOUDINARY UPLOAD WORKED",
                    "result": result,
                })

            return jsonify({
                "success": False,
                "message": "Cloudinary upload returned None",
            }), 500

        except Exception as e:
            return jsonify({
                "success": False,
                "error": type(e).__name__,
                "message": str(e),
            }), 500

    return app

if __name__ == "__main__":
    create_app().run(
        port=5002,
        debug=True,
    )

