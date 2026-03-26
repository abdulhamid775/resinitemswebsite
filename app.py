from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from datetime import datetime, timedelta
from functools import wraps
import os
import shutil
import uuid
from xml.sax.saxutils import escape as xml_escape


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_IMAGES_DIR = os.path.join(BASE_DIR, "static", "images")
SOURCE_IMAGES_DIR = os.path.join(BASE_DIR, "imagesresinnew")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ADMIN_PANEL_PASSWORD = os.environ.get("ADMIN_PANEL_PASSWORD", "change-admin-password")

ORDER_STATUS_FLOW = [
    ("ordered", "Order Placed"),
    ("packed_location", "Items Packed At Location"),
    ("packed_nearest_center", "Packed To Nearest Center"),
    ("shipped", "Shipped"),
    ("arrived", "Arrived"),
]

STATUS_NORMALIZATION = {
    "created": "ordered",
    "pending": "ordered",
    "delivered": "arrived",
}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(BASE_DIR, "database.db"),
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

db = SQLAlchemy(app)


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


SMALL_PRODUCT_KEYWORDS = (
    "keychain",
    "bookmark",
    "magnet",
    "coaster",
    "mini",
    "small",
    "pendant",
)

BIG_PRODUCT_KEYWORDS = (
    "tray",
    "wall",
    "frame",
    "clock",
    "nameplate",
    "plaque",
    "table",
    "big",
    "geode",
    "set",
)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(50), nullable=False)  # "small" or "big"
    image = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    total_price = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), default="pending")
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    name = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    pincode = db.Column(db.String(20))
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))


class OrderItem(db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    price = db.Column(db.Integer, nullable=False)


def init_db():
    with app.app_context():
        db.create_all()
        auto_seed_products_from_images()


def derive_product_details(filename, index):
    stem = os.path.splitext(filename)[0].replace("_", " ").replace("-", " ").strip()
    normalized = stem.lower()

    if any(keyword in normalized for keyword in SMALL_PRODUCT_KEYWORDS):
        category = "small"
        price = 699
    elif any(keyword in normalized for keyword in BIG_PRODUCT_KEYWORDS):
        category = "big"
        price = 2499
    else:
        # Fallback split keeps mixed galleries balanced.
        category = "small" if index % 2 == 0 else "big"
        price = 799 if category == "small" else 2199

    pretty_name = " ".join(word.capitalize() for word in stem.split()) or f"Resin Item {index + 1}"
    if "resin" not in normalized:
        pretty_name = f"{pretty_name} Resin"

    return category, price, pretty_name


def auto_seed_products_from_images():
    """
    Automatically create Product entries from images in SOURCE_IMAGES_DIR.
    Copies files into STATIC_IMAGES_DIR if needed and avoids duplicates.
    """
    if not os.path.isdir(SOURCE_IMAGES_DIR):
        return

    os.makedirs(STATIC_IMAGES_DIR, exist_ok=True)

    image_files = [
        f
        for f in os.listdir(SOURCE_IMAGES_DIR)
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]

    if not image_files:
        return

    for index, filename in enumerate(sorted(image_files)):
        category, price, display_name = derive_product_details(filename, index)

        src_path = os.path.join(SOURCE_IMAGES_DIR, filename)
        dest_path = os.path.join(STATIC_IMAGES_DIR, filename)
        if not os.path.isfile(dest_path):
            try:
                shutil.copy2(src_path, dest_path)
            except OSError:
                continue

        existing = Product.query.filter_by(image=filename).first()
        if existing:
            old_default_name = existing.name.startswith("Handmade Small Resin Gift #") or existing.name.startswith(
                "Handmade Big Resin Art #"
            )
            old_default_price = existing.price in (500, 1000)
            old_default_description = existing.description == "Handmade resin art piece from The Gift Hustle."

            if old_default_name:
                existing.name = display_name
                existing.category = category
            if old_default_price:
                existing.price = price
                existing.category = category
            if old_default_description:
                existing.description = "Handmade resin art piece curated by The Gift Hustle."
            continue

        product = Product(
            name=display_name,
            price=price,
            category=category,
            image=filename,
            description="Handmade resin art piece curated by The Gift Hustle.",
        )
        db.session.add(product)

    db.session.commit()


@app.context_processor
def inject_cart_count():
    cart = session.get("cart", {})
    count = sum(item["quantity"] for item in cart.values())
    return {
        "cart_count": count,
        "product_image_url": product_image_url,
        "current_year": datetime.utcnow().year,
    }


def product_image_url(filename):
    if not filename:
        return url_for("static", filename="images/product-placeholder.svg")
    image_path = os.path.join(STATIC_IMAGES_DIR, filename)
    if os.path.isfile(image_path):
        return url_for("static", filename=f"images/{filename}")
    return url_for("static", filename="images/product-placeholder.svg")


def save_uploaded_product_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None, "No image file selected."

    original_name = secure_filename(file_storage.filename)
    _, extension = os.path.splitext(original_name.lower())
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return None, "Only JPG, JPEG, PNG, and WEBP images are allowed."

    os.makedirs(STATIC_IMAGES_DIR, exist_ok=True)
    unique_name = f"product-{uuid.uuid4().hex}{extension}"
    target_path = os.path.join(STATIC_IMAGES_DIR, unique_name)
    file_storage.save(target_path)
    return unique_name, None


def delete_image_if_unused(filename):
    if not filename:
        return
    if Product.query.filter(Product.image == filename).count() > 0:
        return
    image_path = os.path.join(STATIC_IMAGES_DIR, filename)
    if os.path.isfile(image_path):
        try:
            os.remove(image_path)
        except OSError:
            pass


def normalize_order_status(status):
    status_value = (status or "").strip().lower()
    return STATUS_NORMALIZATION.get(status_value, status_value or "ordered")


def get_order_live_location(status):
    normalized_status = normalize_order_status(status)
    # Demo tracking points for customer live order map.
    status_points = {
        "ordered": {
            "label": "Order received at studio",
            "lat": 23.0225,
            "lng": 72.5714,
            "address": "Ahmedabad Resin Studio",
        },
        "packed_location": {
            "label": "Items packed at origin location",
            "lat": 23.0225,
            "lng": 72.5714,
            "address": "Ahmedabad Packing Center",
        },
        "packed_nearest_center": {
            "label": "Reached nearest dispatch center",
            "lat": 21.1702,
            "lng": 72.8311,
            "address": "Surat Dispatch Hub",
        },
        "shipped": {
            "label": "Shipped and in transit",
            "lat": 22.3072,
            "lng": 73.1812,
            "address": "Vadodara Transit Route",
        },
        "arrived": {
            "label": "Arrived at destination city",
            "lat": 19.0760,
            "lng": 72.8777,
            "address": "Destination Delivery Center",
        },
        "cancelled": {
            "label": "Order cancelled",
            "lat": 23.0225,
            "lng": 72.5714,
            "address": "No live movement (cancelled)",
        },
    }
    return status_points.get(normalized_status, status_points["ordered"])


def get_route_points_until_status(status):
    ordered_flow = ["ordered", "packed_location", "packed_nearest_center", "shipped", "arrived"]
    normalized_status = normalize_order_status(status)
    if normalized_status not in ordered_flow:
        normalized_status = "ordered"
    end_index = ordered_flow.index(normalized_status)
    route_points = []
    for key in ordered_flow[: end_index + 1]:
        point = get_order_live_location(key)
        route_points.append(
            {
                "key": key,
                "label": point["label"],
                "lat": point["lat"],
                "lng": point["lng"],
                "address": point["address"],
            }
        )
    return route_points


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Please log in to access admin panel.", "warning")
            return redirect(url_for("admin_login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


@app.route("/")
def index():
    featured_products = Product.query.limit(8).all()
    best_sellers = Product.query.limit(4).all()
    return render_template("index.html", featured_products=featured_products, best_sellers=best_sellers)


@app.route("/shop")
def shop():
    filter_category = request.args.get("category")
    search_term = (request.args.get("q") or "").strip()
    query = Product.query
    if filter_category in ("small", "big"):
        query = query.filter_by(category=filter_category)
    if search_term:
        like_pattern = f"%{search_term}%"
        query = query.filter(
            or_(
                Product.name.ilike(like_pattern),
                Product.description.ilike(like_pattern),
            )
        )
    products = query.all()
    return render_template(
        "shop.html",
        products=products,
        filter_category=filter_category,
        search_term=search_term,
    )


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    return render_template("product.html", product=product)


@app.route("/robots.txt")
def robots_txt():
    base_url = request.url_root.rstrip("/")
    sitemap_url = f"{base_url}/sitemap.xml"
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /login",
        "Disallow: /signup",
        "Disallow: /cart",
        "Disallow: /checkout",
        "Disallow: /update-cart",
        "Disallow: /order",
        "Disallow: /order/",
        f"Sitemap: {sitemap_url}",
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    base_url = request.url_root.rstrip("/")
    today = datetime.utcnow().date().isoformat()

    static_paths = [
        (url_for("index"), "daily", 1.0),
        (url_for("shop"), "weekly", 0.8),
        (url_for("about"), "yearly", 0.6),
        (url_for("contact"), "yearly", 0.6),
    ]

    products = Product.query.order_by(Product.id.desc()).all()
    product_paths = [url_for("product_detail", product_id=p.id) for p in products]

    url_entries = []
    for path, changefreq, priority in static_paths + [
        (path, "weekly", 0.7) for path in product_paths
    ]:
        loc = f"{base_url}{path}"
        url_entries.append(
            "<url>"
            f"<loc>{xml_escape(loc)}</loc>"
            f"<lastmod>{today}</lastmod>"
            f"<changefreq>{xml_escape(changefreq)}</changefreq>"
            f"<priority>{priority}</priority>"
            "</url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(url_entries)
        + "</urlset>"
    )
    return Response(xml, mimetype="application/xml")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        flash("Thank you for reaching out! We'll get back to you soon.", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html")


@app.route("/add-to-cart/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    product = Product.query.get_or_404(product_id)
    quantity = int(request.form.get("quantity", 1))

    cart = session.get("cart", {})
    key = str(product.id)
    if key in cart:
        cart[key]["quantity"] += quantity
    else:
        cart[key] = {
            "name": product.name,
            "price": product.price,
            "image": product.image,
            "quantity": quantity,
        }
    session["cart"] = cart
    flash("Added to cart.", "success")
    return redirect(request.referrer or url_for("shop"))


@app.route("/cart")
def cart():
    cart = session.get("cart", {})
    total = sum(item["price"] * item["quantity"] for item in cart.values())
    return render_template("cart.html", cart=cart, total=total)


@app.route("/update-cart", methods=["POST"])
def update_cart():
    cart = session.get("cart", {})
    for product_id, item in list(cart.items()):
        quantity = int(request.form.get(f"quantity_{product_id}", item["quantity"]))
        if quantity <= 0:
            cart.pop(product_id)
        else:
            item["quantity"] = quantity
    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart = session.get("cart", {})
    if not cart:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("shop"))

    total = sum(item["price"] * item["quantity"] for item in cart.values())

    if request.method == "POST":
        name = request.form.get("name")
        phone = request.form.get("phone")
        address = request.form.get("address")
        pincode = request.form.get("pincode")
        city = request.form.get("city")
        state = request.form.get("state")

        order = Order(
            user_id=None,
            total_price=total,
            name=name,
            phone=phone,
            address=address,
            pincode=pincode,
            city=city,
            state=state,
            status="ordered",
        )
        db.session.add(order)
        db.session.commit()

        for product_id, item in cart.items():
            order_item = OrderItem(
                order_id=order.id,
                product_id=int(product_id),
                quantity=item["quantity"],
                price=item["price"],
            )
            db.session.add(order_item)
        db.session.commit()

        session.pop("cart", None)
        session["last_order_id"] = order.id
        session["last_order_phone"] = phone or ""
        flash("Order placed successfully! You can cancel from order details below.", "success")
        return redirect(url_for("order_success", order_id=order.id))

    return render_template("checkout.html", cart=cart, total=total)


@app.route("/order/success/<int:order_id>")
def order_success(order_id):
    order = Order.query.get_or_404(order_id)
    items = OrderItem.query.filter_by(order_id=order.id).all()
    normalized_status = normalize_order_status(order.status)
    can_cancel = (
        session.get("last_order_id") == order.id
        and session.get("last_order_phone") == (order.phone or "")
        and normalized_status in ("ordered", "packed_location", "packed_nearest_center")
    )
    ordered_at = order.order_date or datetime.utcnow()
    expected_ship_date = ordered_at + timedelta(days=1)
    expected_delivery_date = ordered_at + timedelta(days=5)
    tracking_dates = {
        "ordered": ordered_at,
        "packed_location": ordered_at + timedelta(days=1),
        "packed_nearest_center": ordered_at + timedelta(days=2),
        "shipped": ordered_at + timedelta(days=3),
        "arrived": ordered_at + timedelta(days=5),
    }
    tracking_steps = [
        {"key": key, "label": label, "date": tracking_dates[key]}
        for key, label in ORDER_STATUS_FLOW
    ]
    step_order = [step["key"] for step in tracking_steps]
    current_step_index = step_order.index(normalized_status) if normalized_status in step_order else -1
    return render_template(
        "order_success.html",
        order=order,
        items=items,
        can_cancel=can_cancel,
        expected_ship_date=expected_ship_date,
        expected_delivery_date=expected_delivery_date,
        current_status=normalized_status,
        tracking_steps=tracking_steps,
        current_step_index=current_step_index,
    )


@app.route("/order/cancel/<int:order_id>", methods=["POST"])
def cancel_my_order(order_id):
    order = Order.query.get_or_404(order_id)
    if session.get("last_order_id") != order.id or session.get("last_order_phone") != (order.phone or ""):
        flash("You are not allowed to cancel this order from this device/session.", "danger")
        return redirect(url_for("order_success", order_id=order.id))

    if normalize_order_status(order.status) == "cancelled" or order.status == "cancelled":
        flash("This order is already cancelled.", "warning")
        return redirect(url_for("order_success", order_id=order.id))

    order.status = "cancelled"
    db.session.commit()
    flash("Your order has been cancelled.", "info")
    return redirect(url_for("order_success", order_id=order.id))


@app.route("/order/live/<int:order_id>")
def live_order_tracking(order_id):
    order = Order.query.get_or_404(order_id)
    if session.get("last_order_id") != order.id or session.get("last_order_phone") != (order.phone or ""):
        flash("Live tracking is available from the same device/session used for order placement.", "warning")
        return redirect(url_for("order_success", order_id=order.id))

    live_point = get_order_live_location(order.status)
    route_points = get_route_points_until_status(order.status)
    ordered_at = order.order_date or datetime.utcnow()
    expected_delivery_date = ordered_at + timedelta(days=5)
    days_left = max(0, (expected_delivery_date.date() - datetime.utcnow().date()).days)
    eta_text = "Out for delivery today" if days_left == 0 else f"Estimated delivery in {days_left} day(s)"
    return render_template(
        "order_live_map.html",
        order=order,
        live_point=live_point,
        current_status=normalize_order_status(order.status),
        route_points=route_points,
        eta_text=eta_text,
        expected_delivery_date=expected_delivery_date,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            flash("Logged in successfully.", "success")
            return redirect(url_for("index"))
        flash("Invalid credentials.", "danger")
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("Email already registered.", "warning")
            return redirect(url_for("signup"))
        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out.", "info")
    return redirect(url_for("index"))


@app.route("/admin/orders")
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.order_date.desc()).all()
    order_items_map = {}
    order_status_map = {}
    for order in orders:
        items = OrderItem.query.filter_by(order_id=order.id).all()
        order_items_map[order.id] = items
        order_status_map[order.id] = normalize_order_status(order.status)
    return render_template(
        "admin_orders.html",
        orders=orders,
        order_items_map=order_items_map,
        order_status_map=order_status_map,
    )


@app.route("/admin/orders/<int:order_id>/cancel", methods=["POST"])
@admin_required
def cancel_order(order_id):
    order = Order.query.get_or_404(order_id)
    if normalize_order_status(order.status) != "cancelled" and order.status != "cancelled":
        order.status = "cancelled"
        db.session.commit()
        flash(f"Order #{order.id} cancelled.", "info")
    else:
        flash(f"Order #{order.id} is already cancelled.", "warning")
    return redirect(url_for("admin_orders"))


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    new_status = (request.form.get("status") or "").strip().lower()
    allowed_statuses = {"ordered", "packed_location", "packed_nearest_center", "shipped", "arrived", "cancelled"}
    if new_status not in allowed_statuses:
        flash("Invalid order status.", "danger")
        return redirect(url_for("admin_orders"))

    order.status = new_status
    db.session.commit()
    flash(f"Order #{order.id} marked as {new_status}.", "success")
    return redirect(url_for("admin_orders"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    total_products = Product.query.count()
    total_orders = Order.query.count()
    cancelled_orders = Order.query.filter_by(status="cancelled").count()
    return render_template(
        "admin_dashboard.html",
        total_products=total_products,
        total_orders=total_orders,
        cancelled_orders=cancelled_orders,
    )


@app.route("/admin/products", methods=["GET", "POST"])
@admin_required
def admin_products():
    if request.method == "POST":
        product_id = request.form.get("product_id", type=int)
        product = Product.query.get_or_404(product_id)

        product_name = (request.form.get("name") or "").strip()
        product_description = (request.form.get("description") or "").strip()
        product_price = request.form.get("price", type=int)
        remove_image = request.form.get("remove_image") == "1"
        image_file = request.files.get("image_file")

        old_image = product.image

        if product_name:
            product.name = product_name
        if product_description:
            product.description = product_description
        if product_price and product_price > 0:
            product.price = product_price
        # Upload always takes precedence over remove, to avoid accidental blank images.
        if image_file and image_file.filename:
            new_image_name, error_message = save_uploaded_product_image(image_file)
            if error_message:
                flash(error_message, "danger")
                return redirect(url_for("admin_products"))
            product.image = new_image_name
        elif remove_image:
            product.image = ""

        db.session.commit()
        if remove_image:
            delete_image_if_unused(old_image)
        elif image_file and image_file.filename:
            delete_image_if_unused(old_image)
        flash("Product updated successfully.", "success")
        return redirect(url_for("admin_products"))

    products = Product.query.order_by(Product.id.desc()).all()
    return render_template("admin_products.html", products=products)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password") or ""
        next_url = request.form.get("next") or url_for("admin_dashboard")
        if password == ADMIN_PANEL_PASSWORD:
            session["is_admin"] = True
            flash("Admin login successful.", "success")
            return redirect(next_url)
        flash("Invalid admin password.", "danger")
    next_url = request.args.get("next") or request.form.get("next") or url_for("admin_dashboard")
    return render_template("admin_login.html", next_url=next_url)


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Admin logged out.", "info")
    return redirect(url_for("index"))


# Gunicorn (Render/production) never runs __main__ — initialize DB when the app loads.
with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )

