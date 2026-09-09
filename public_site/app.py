import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, abort, flash, session,
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from common.db import get_db
from common.config import Config
from common.cloudinary_utils import upload_to_cloudinary


def current_user():
    """Look up the logged-in user from the session, or None."""
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().find_one("users", {"_id": uid})


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

    # ── Context ───────────────────────────────────────────────────────────────
    @app.context_processor
    def inject_globals():
        db = get_db()
        return dict(
            nav_categories=db.find("categories", {}, sort=[("sort_order", 1)]),
            site_name="NexusVault",
            year=datetime.now().year,
            current_user=current_user(),
        )

    # ── TEMP DEBUG — catches the real exception behind the 500 on / ─────────
    @app.route("/debug-index-error")
    def debug_index_error():
        import traceback
        try:
            db = get_db()
            page = 1
            per_page = Config.ITEMS_PER_PAGE
            skip = 0

            featured = db.find("items", {"is_approved": True, "is_featured": True}, limit=6)
            items = db.find("items", {"is_approved": True}, skip=skip, limit=per_page)
            total = db.count("items", {"is_approved": True})
            total_pages = max(1, (total + per_page - 1) // per_page)

            stats = {
                "total":  total,
                "links":  db.count("items", {"type": "link",  "is_approved": True}),
                "videos": db.count("items", {"type": "video", "is_approved": True}),
                "images": db.count("items", {"type": "image", "is_approved": True}),
                "files":  db.count("items", {"type": "file",  "is_approved": True}),
            }
            return jsonify({"ok": True, "stats": stats, "total_pages": total_pages})
        except Exception as e:
            return jsonify({
                "ok": False,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc(),
            }), 200

    # ── Home ──────────────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        db = get_db()
        page = request.args.get("page", 1, type=int)
        per_page = Config.ITEMS_PER_PAGE
        skip = (page - 1) * per_page
        user = current_user()
        hidden = user["hidden"] if user else []

        featured = [i for i in db.find("items", {"is_approved": True, "is_featured": True}, limit=6) if i["_id"] not in hidden]
        items = [i for i in db.find("items", {"is_approved": True}, skip=skip, limit=per_page) if i["_id"] not in hidden]
        total = db.count("items", {"is_approved": True})
        total_pages = max(1, (total + per_page - 1) // per_page)

        stats = {
            "total":  total,
            "links":  db.count("items", {"type": "link",  "is_approved": True}),
            "videos": db.count("items", {"type": "video", "is_approved": True}),
            "images": db.count("items", {"type": "image", "is_approved": True}),
            "files":  db.count("items", {"type": "file",  "is_approved": True}),
        }
        return render_template("index.html", featured=featured, items=items,
                               page=page, total_pages=total_pages, total=total,
                               stats=stats, active="home")

    # ── Categories page ──────────────────────────────────────────────────────
    @app.route("/categories")
    def categories_page():
        db = get_db()
        cats = db.find("categories", {}, sort=[("sort_order", 1)])
        counts = {c["slug"]: db.count("items", {"category_slug": c["slug"], "is_approved": True}) for c in cats}
        return render_template("categories_page.html", categories=cats, counts=counts, active="categories")

    # ── Category ──────────────────────────────────────────────────────────────
    @app.route("/category/<slug>")
    def category(slug):
        db = get_db()
        cat = db.find_one("categories", {"slug": slug})
        if not cat:
            abort(404)

        page = request.args.get("page", 1, type=int)
        itype = request.args.get("type")
        per_page = Config.ITEMS_PER_PAGE
        skip = (page - 1) * per_page
        user = current_user()
        hidden = user["hidden"] if user else []

        q = {"is_approved": True, "category_slug": slug}
        if itype:
            q["type"] = itype

        items = [i for i in db.find("items", q, skip=skip, limit=per_page) if i["_id"] not in hidden]
        total = db.count("items", q)
        total_pages = max(1, (total + per_page - 1) // per_page)

        return render_template("category.html", category=cat, items=items,
                               page=page, total_pages=total_pages, total=total,
                               itype=itype, active="cat-" + slug)

    # ── Zone / Server ─────────────────────────────────────────────────────────
    @app.route("/zone/<slug>")
    def zone(slug):
        db = get_db()
        z = db.find_one("zones", {"slug": slug})
        if not z:
            abort(404)

        page = request.args.get("page", 1, type=int)
        per_page = Config.ITEMS_PER_PAGE
        skip = (page - 1) * per_page
        user = current_user()
        hidden = user["hidden"] if user else []

        q = {"is_approved": True, "zone_slug": slug}
        items = [i for i in db.find("items", q, skip=skip, limit=per_page) if i["_id"] not in hidden]
        total = db.count("items", q)
        total_pages = max(1, (total + per_page - 1) // per_page)

        return render_template("zone.html", zone=z, items=items,
                               page=page, total_pages=total_pages, total=total,
                               active="servers")

    # ── Servers list ──────────────────────────────────────────────────────────
    @app.route("/servers")
    def servers():
        db = get_db()
        all_zones = db.find("zones", {}, sort=[("sort_order", 1)])
        top = [z for z in all_zones if not z.get("parent_slug")]
        subs = {}
        for z in all_zones:
            if z.get("parent_slug"):
                subs.setdefault(z["parent_slug"], []).append(z)
        counts = {z["slug"]: db.count("items", {"zone_slug": z["slug"], "is_approved": True})
                  for z in all_zones}
        return render_template("servers.html", top_zones=top, sub_zones=subs,
                               zone_counts=counts, active="servers")

    # ── New uploads ───────────────────────────────────────────────────────────
    @app.route("/new")
    def new():
        db = get_db()
        page = request.args.get("page", 1, type=int)
        per_page = Config.ITEMS_PER_PAGE
        skip = (page - 1) * per_page
        user = current_user()
        hidden = user["hidden"] if user else []
        items = [i for i in db.find("items", {"is_approved": True}, skip=skip, limit=per_page) if i["_id"] not in hidden]
        total = db.count("items", {"is_approved": True})
        total_pages = max(1, (total + per_page - 1) // per_page)
        return render_template("new.html", items=items, page=page,
                               total_pages=total_pages, total=total, active="new")

    # ── TEMP DEBUG — remove after diagnosing upload issue ───────────────────
    @app.route("/debug-upload", methods=["GET", "POST"])
    def debug_upload():
        if request.method == "GET":
            return """
            <h3>Debug upload — pick a file and submit</h3>
            <form method="POST" enctype="multipart/form-data">
              <p>Video input: <input type="file" name="file" accept="video/*"></p>
              <p>Image input: <input type="file" name="file" accept="image/*"></p>
              <p>File input: <input type="file" name="file"></p>
              <button type="submit">Submit</button>
            </form>
            """
        all_files = request.files.getlist("file")
        report = {
            "content_type_header": request.content_type,
            "content_length_header": request.content_length,
            "form_keys": list(request.form.keys()),
            "files_count": len(all_files),
            "files": [
                {
                    "index": i,
                    "filename": f.filename,
                    "content_type": f.content_type,
                    "has_filename": bool(f.filename),
                }
                for i, f in enumerate(all_files)
            ],
        }
        return jsonify(report)

    # ── TEMP DEBUG — real signed upload test, shows Cloudinary's raw error ──
    @app.route("/debug-cloudinary-real")
    def debug_cloudinary_real():
        import io
        from common.cloudinary_utils import upload_to_cloudinary, _credentials

        creds = _credentials()
        if not creds:
            return jsonify({"error": "credentials not configured"})

        cloud_name, api_key, api_secret = creds

        # tiny 1x1 red PNG, hardcoded bytes, so this is a real signed
        # upload attempt with no dependency on user-picked files
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108"
            "0600000031cf3894000000015253474400aece1ce9000000"
            "0467414d410000b18f0bfc6105000000097048597300000e"
            "c300000ec301c76fa8640000000c4944415478da6360606060"
            "0000000500010d0a2db4000000004945454e44ae426082"
        )

        class FakeFile:
            filename = "debug-test.png"
            def read(self_inner):
                return png_bytes

        result = upload_to_cloudinary(FakeFile(), "image")

        return jsonify({
            "cloud_name": cloud_name,
            "api_key_length": len(api_key),
            "api_key_preview": api_key[:4] + "..." + api_key[-2:] if len(api_key) > 6 else "(too short)",
            "api_secret_length": len(api_secret),
            "upload_result": result,
            "note": "If upload_result is null, check your Worker logs (wrangler tail) for the '[Cloudinary] Upload failed (...)' line — it has the exact reason.",
        })

    # ── Suggest a site ────────────────────────────────────────────────────────
    @app.route("/suggest", methods=["GET", "POST"])
    def suggest():
        db = get_db()
        cats = db.find("categories", {}, sort=[("sort_order", 1)])
        prefill = request.args.get("q", "").strip()
        err = success = None
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            url = request.form.get("url", "").strip()
            cat = request.form.get("category_slug", "public")
            desc = request.form.get("description", "").strip()
            if not name:
                err = "Name is required."
            elif not url:
                err = "URL is required."
            else:
                db.insert("suggestions", {
                    "name": name,
                    "url": url,
                    "category_slug": cat,
                    "description": desc,
                    "status": "pending",
                    "created_at": datetime.utcnow().isoformat(),
                })
                success = True
        return render_template("suggest.html", categories=cats,
                               prefill=prefill, error=err, success=success, active="")

    # ── Upload ────────────────────────────────────────────────────────────────
    @app.route("/upload", methods=["GET", "POST"])
    def upload():
        db = get_db()
        cats = db.find("categories", {}, sort=[("sort_order", 1)])
        zones = db.find("zones",      {}, sort=[("sort_order", 1)])
        err = success = None

        if request.method == "POST":
            itype    = request.form.get("type", "link")
            title    = request.form.get("title", "").strip()
            desc     = request.form.get("description", "").strip()
            cat_slug = request.form.get("category_slug", "public")
            z_slug   = request.form.get("zone_slug", "global")
            tags     = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()]
            uploader = request.form.get("uploader_name", "Anonymous").strip() or "Anonymous"

            if not title:
                err = "Title is required."
            else:
                item = dict(
                    title=title, description=desc, type=itype,
                    source_type="public", category_slug=cat_slug, zone_slug=z_slug,
                    tags=tags, uploader_name=uploader,
                    is_approved=False, is_featured=False,
                    views=0, downloads=0,
                    url="", thumbnail_url="", cloudinary_id="", cloudinary_url="",
                    extra_details={},
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                )
                if itype == "link":
                    item["url"] = request.form.get("url", "").strip()
                    item["thumbnail_url"] = request.form.get("thumbnail_url", "").strip()
                else:
                    # NOTE: upload.html contains multiple file inputs that
                    # all share name="file" (one per type tab: video/image/
                    # file). request.files.get("file") only returns the
                    # FIRST one in the multipart body, which is usually
                    # empty since only one tab's input actually has a file
                    # selected. Use getlist() and pick the one that's
                    # actually populated — same fix already applied in
                    # admin_site/app.py.
                    uploaded_files = request.files.getlist("file")
                    file = next(
                        (f for f in uploaded_files
                         if f is not None and getattr(f, "filename", "")),
                        None,
                    )
                    ext_url = request.form.get("external_url", "").strip()
                    if file and file.filename:
                        res = upload_to_cloudinary(file, itype)
                        if res:
                            item.update(cloudinary_id=res["public_id"],
                                        cloudinary_url=res["url"], url=res["url"],
                                        thumbnail_url=res.get("thumbnail_url", res["url"]))
                        else:
                            err = "Upload failed — check Cloudinary config."
                    elif ext_url:
                        item["url"] = ext_url
                        item["thumbnail_url"] = request.form.get("thumbnail_url", "").strip()
                    else:
                        err = "Provide a file or an external URL."

                if not err:
                    db.insert("items", item)
                    success = True

        return render_template("upload.html", categories=cats, zones=zones,
                               error=err, success=success, active="upload")

    # ── Item detail ───────────────────────────────────────────────────────────
    @app.route("/item/<item_id>")
    def item_detail(item_id):
        db = get_db()
        item = db.find_one("items", {"_id": item_id, "is_approved": True})
        if not item:
            abort(404)
        db.update("items", {"_id": item_id}, {"$inc": {"views": 1}})
        item["views"] = item.get("views", 0) + 1

        discs = db.find("discussions", {"item_id": item_id, "parent_id": None})
        for d in discs:
            d["replies"] = db.find("discussions", {"parent_id": d["_id"]})

        related = [r for r in db.find("items",
                   {"is_approved": True, "category_slug": item.get("category_slug", "")},
                   limit=8) if r["_id"] != item_id][:4]

        cat  = db.find_one("categories", {"slug": item.get("category_slug", "")})
        zone = db.find_one("zones",      {"slug": item.get("zone_slug", "")})

        return render_template("item.html", item=item, discussions=discs,
                               related=related, category=cat, zone=zone, active="")

    # ── Add discussion ────────────────────────────────────────────────────────
    @app.route("/item/<item_id>/discuss", methods=["POST"])
    def add_discussion(item_id):
        db = get_db()
        if not db.find_one("items", {"_id": item_id, "is_approved": True}):
            abort(404)
        msg = request.form.get("message", "").strip()
        if not msg:
            return redirect(url_for("item_detail", item_id=item_id))
        db.insert("discussions", {
            "item_id":   item_id,
            "username":  (request.form.get("username") or "Anonymous").strip(),
            "message":   msg,
            "parent_id": request.form.get("parent_id") or None,
            "created_at": datetime.utcnow(),
        })
        return redirect(url_for("item_detail", item_id=item_id) + "#disc-section")

    # ── Search ────────────────────────────────────────────────────────────────
    @app.route("/search")
    def search():
        db = get_db()
        q        = request.args.get("q", "").strip()
        itype    = request.args.get("type")
        cat_slug = request.args.get("category")
        z_slug   = request.args.get("zone")
        page     = request.args.get("page", 1, type=int)
        per_page = Config.ITEMS_PER_PAGE
        user = current_user()
        hidden = user["hidden"] if user else []
        items = []; total = 0

        if q:
            extra = {"is_approved": True}
            if itype:    extra["type"] = itype
            if cat_slug: extra["category_slug"] = cat_slug
            if z_slug:   extra["zone_slug"] = z_slug
            all_res = [i for i in db.search("items", q, extra) if i["_id"] not in hidden]
            total   = len(all_res)
            skip    = (page - 1) * per_page
            items   = all_res[skip: skip + per_page]

        total_pages = max(1, (total + per_page - 1) // per_page)
        zones = db.find("zones", {}, sort=[("sort_order", 1)])
        return render_template("search.html", items=items, q=q,
                               page=page, total_pages=total_pages, total=total,
                               itype=itype, cat_slug=cat_slug, z_slug=z_slug,
                               zones=zones, active="search")

    # ── Download ──────────────────────────────────────────────────────────────
    @app.route("/download/<item_id>")
    def download(item_id):
        db = get_db()
        item = db.find_one("items", {"_id": item_id, "is_approved": True})
        if not item:
            abort(404)
        db.update("items", {"_id": item_id}, {"$inc": {"downloads": 1}})
        target = item.get("cloudinary_url") or item.get("url", "")
        if target:
            return redirect(target)
        abort(404)


    # ── Cloudinary connectivity test (Termux helper) ──────────────────────────
    @app.route("/test-cloudinary")
    def test_cloudinary():
        from common.cloudinary_utils import test_cloudinary_connection
        import os
        result = test_cloudinary_connection()
        return jsonify({
            "cloudinary_configured": bool(Config.CLOUDINARY_CLOUD_NAME),
            "cloud_name": Config.CLOUDINARY_CLOUD_NAME or "(not set)",
            "ssl_cert_file": os.environ.get("SSL_CERT_FILE", "(not set)"),
            "requests_ca_bundle": os.environ.get("REQUESTS_CA_BUNDLE", "(not set)"),
            "connectivity_test": result,
            "hint": "If ok=false on Termux: export SSL_CERT_FILE=$PREFIX/etc/tls/cert.pem && export REQUESTS_CA_BUNDLE=$PREFIX/etc/tls/cert.pem then restart server."
        })

    # ── API stats ─────────────────────────────────────────────────────────────
    @app.route("/api/stats")
    def api_stats():
        db = get_db()
        return jsonify({
            "total":  db.count("items", {"is_approved": True}),
            "links":  db.count("items", {"type": "link",  "is_approved": True}),
            "videos": db.count("items", {"type": "video", "is_approved": True}),
            "images": db.count("items", {"type": "image", "is_approved": True}),
            "files":  db.count("items", {"type": "file",  "is_approved": True}),
            "categories": db.count("categories", {}),
            "zones":  db.count("zones", {}),
        })

    # ══════════════════════════════════════════════════════════════════════════
    #  USER ACCOUNTS
    # ══════════════════════════════════════════════════════════════════════════

    @app.route("/register", methods=["GET", "POST"])
    def register():
        db = get_db()
        cats = db.find("categories", {}, sort=[("sort_order", 1)])
        err = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            pref_cats = request.form.getlist("pref_categories")

            if not username or len(username) < 3:
                err = "Username must be at least 3 characters."
            elif not password or len(password) < 6:
                err = "Password must be at least 6 characters."
            elif db.find_one("users", {"username": username}):
                err = "Username already taken."
            else:
                db.insert("users", {
                    "username": username,
                    "password_hash": generate_password_hash(password),
                    "pref_categories": pref_cats,
                    "favorites": [],
                    "hidden": [],
                    "created_at": datetime.utcnow().isoformat(),
                })
                flash("Account created! Please log in.", "success")
                return redirect(url_for("login"))
        return render_template("register.html", categories=cats, error=err, active="")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("user_id"):
            return redirect(url_for("index"))
        err = None
        if request.method == "POST":
            db = get_db()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = db.find_one("users", {"username": username})
            if user and check_password_hash(user.get("password_hash", ""), password):
                session["user_id"] = user["_id"]
                session["username"] = user["username"]
                flash(f"Welcome back, {user['username']}!", "success")
                return redirect(request.args.get("next") or url_for("index"))
            err = "Invalid username or password."
        return render_template("login_page.html", error=err, active="")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Logged out.", "info")
        return redirect(url_for("index"))

    # ── My Collection (Favorites) ────────────────────────────────────────────
    @app.route("/my-collection")
    def my_collection():
        user = current_user()
        if not user:
            return redirect(url_for("login", next=url_for("my_collection")))
        db = get_db()
        itype = request.args.get("type", "").strip()
        page = request.args.get("page", 1, type=int)
        per_page = Config.ITEMS_PER_PAGE
        favs = user.get("favorites", [])

        all_items = []
        for fid in favs:
            item = db.find_one("items", {"_id": fid, "is_approved": True})
            if item and (not itype or item.get("type") == itype):
                all_items.append(item)

        total = len(all_items)
        items = all_items[(page - 1) * per_page: (page - 1) * per_page + per_page]
        total_pages = max(1, (total + per_page - 1) // per_page)
        return render_template("my_collection.html", items=items, page=page,
                               total_pages=total_pages, total=total, itype=itype,
                               active="collection")

    # ── Toggle favorite ──────────────────────────────────────────────────────
    @app.route("/favorite/<item_id>", methods=["POST"])
    def toggle_favorite(item_id):
        user = current_user()
        if not user:
            return jsonify({"error": "login_required"}), 401
        db = get_db()
        favs = user.get("favorites", [])
        if item_id in favs:
            db.update("users", {"_id": user["_id"]}, {"$pull": {"favorites": item_id}})
            return jsonify({"ok": True, "saved": False})
        else:
            db.update("users", {"_id": user["_id"]}, {"$addToSet": {"favorites": item_id}})
            return jsonify({"ok": True, "saved": True})

    # ── Hide/remove item from user's view ────────────────────────────────────
    @app.route("/hide/<item_id>", methods=["POST"])
    def hide_item(item_id):
        user = current_user()
        if not user:
            return jsonify({"error": "login_required"}), 401
        db = get_db()
        hidden = user.get("hidden", [])
        if item_id in hidden:
            db.update("users", {"_id": user["_id"]}, {"$pull": {"hidden": item_id}})
            return jsonify({"ok": True, "hidden": False})
        else:
            db.update("users", {"_id": user["_id"]}, {"$addToSet": {"hidden": item_id}})
            return jsonify({"ok": True, "hidden": True})

    # ── Remove from favorites (from collection page) ────────────────────────
    @app.route("/unfavorite/<item_id>", methods=["POST"])
    def unfavorite(item_id):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        db = get_db()
        db.update("users", {"_id": user["_id"]}, {"$pull": {"favorites": item_id}})
        flash("Removed from your collection.", "info")
        return redirect(request.referrer or url_for("my_collection"))

    # ── Profile / settings ───────────────────────────────────────────────────
    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        db = get_db()
        cats = db.find("categories", {}, sort=[("sort_order", 1)])
        msg = None
        if request.method == "POST":
            pref_cats = request.form.getlist("pref_categories")
            db.update("users", {"_id": user["_id"]}, {"$set": {"pref_categories": pref_cats}})
            msg = "Preferences saved."
        user = current_user()  # refresh
        return render_template("profile.html", user=user, categories=cats, msg=msg, active="")

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    return app


if __name__ == "__main__":
    create_app().run(port=5001, debug=True)
