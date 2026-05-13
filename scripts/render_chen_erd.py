from matplotlib import pyplot as plt
from matplotlib.patches import Ellipse, Polygon, Rectangle


def rect(ax, x, y, w, h, text):
    ax.add_patch(Rectangle((x - w / 2, y - h / 2), w, h, fill=False, linewidth=1.8))
    ax.text(x, y, text, ha="center", va="center", fontsize=9, family="DejaVu Sans")


def oval(ax, x, y, w, h, text):
    ax.add_patch(Ellipse((x, y), w, h, fill=False, linewidth=1.4))
    ax.text(x, y, text, ha="center", va="center", fontsize=7.5, family="DejaVu Sans")


def diamond(ax, x, y, w, h, text):
    points = [(x, y + h / 2), (x + w / 2, y), (x, y - h / 2), (x - w / 2, y)]
    ax.add_patch(Polygon(points, closed=True, fill=False, linewidth=1.8))
    ax.text(x, y, text, ha="center", va="center", fontsize=8, family="DejaVu Sans")


def edge(ax, x1, y1, x2, y2, label=None):
    ax.plot([x1, x2], [y1, y2], color="black", linewidth=1.0)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my + 0.08, label, fontsize=7, ha="center", va="bottom")


fig, ax = plt.subplots(figsize=(24, 12), dpi=200)
ax.set_xlim(0, 24)
ax.set_ylim(0, 12)
ax.axis("off")

# Entities (rectangles)
rect(ax, 3.0, 8.5, 2.8, 1.1, "USER")
rect(ax, 7.2, 10.4, 3.1, 1.0, "USER_PROFILE")
rect(ax, 7.2, 6.6, 3.1, 1.0, "USER_KEYWORD")
rect(ax, 11.0, 10.4, 3.5, 1.0, "USER_PREFERENCE")
rect(ax, 11.0, 8.2, 3.1, 1.0, "USER_FOLLOW")
rect(ax, 11.0, 6.0, 3.1, 1.0, "NOTIFICATION")
rect(ax, 11.0, 3.8, 3.1, 1.0, "DEVICE_TOKEN")
rect(ax, 14.7, 7.1, 2.8, 1.0, "BOOKMARK")
rect(ax, 14.7, 4.9, 2.8, 1.0, "REACTION")
rect(ax, 18.5, 8.4, 3.0, 1.0, "RAW_ARTICLE")
rect(ax, 22.0, 8.4, 3.5, 1.0, "PROCESSED_ARTICLE")

# USER attributes (ovals)
oval(ax, 1.0, 10.2, 2.1, 0.72, "id (PK)")
oval(ax, 1.0, 9.3, 2.1, 0.72, "email (UQ)")
oval(ax, 1.0, 8.4, 2.1, 0.72, "role")
oval(ax, 1.0, 7.5, 2.1, 0.72, "is_active")
oval(ax, 1.0, 6.6, 2.1, 0.72, "created_at")
edge(ax, 2.0, 10.2, 1.9, 9.0)
edge(ax, 2.0, 9.3, 1.9, 8.8)
edge(ax, 2.0, 8.4, 1.9, 8.6)
edge(ax, 2.0, 7.5, 1.9, 8.4)
edge(ax, 2.0, 6.6, 1.9, 8.2)

# USER_PROFILE attributes
oval(ax, 7.2, 11.6, 2.4, 0.72, "user_id (UQ)")
oval(ax, 5.2, 10.8, 2.4, 0.72, "username")
oval(ax, 9.2, 10.8, 2.2, 0.72, "phone")
edge(ax, 7.2, 11.25, 7.2, 10.9)
edge(ax, 5.9, 10.8, 6.1, 10.6)
edge(ax, 8.5, 10.8, 8.3, 10.6)

# USER_KEYWORD attributes
oval(ax, 7.2, 5.4, 2.4, 0.72, "user_id (UQ)")
oval(ax, 5.2, 6.2, 2.4, 0.72, "keywords[]")
edge(ax, 7.2, 5.75, 7.2, 6.1)
edge(ax, 5.9, 6.2, 6.1, 6.4)

# RAW/PROCESSED attributes
oval(ax, 18.5, 9.6, 2.6, 0.72, "canonical_url (UQ)")
oval(ax, 17.0, 8.3, 2.1, 0.72, "source_key")
oval(ax, 20.0, 8.3, 2.0, 0.72, "title")
edge(ax, 18.5, 9.25, 18.5, 8.9)
edge(ax, 17.6, 8.3, 17.1, 8.4)
edge(ax, 19.4, 8.3, 19.9, 8.4)
oval(ax, 22.0, 9.6, 2.6, 0.72, "canonical_url (UQ)")
oval(ax, 21.0, 8.3, 2.4, 0.72, "credibility_label")
oval(ax, 23.1, 8.3, 2.4, 0.72, "topic_keywords[]")
edge(ax, 22.0, 9.25, 22.0, 8.9)
edge(ax, 21.6, 8.3, 21.2, 8.4)
edge(ax, 22.4, 8.3, 22.9, 8.4)

# Relationships (diamonds)
diamond(ax, 5.2, 9.4, 2.2, 1.1, "HAS_PROFILE")
diamond(ax, 5.2, 7.0, 2.5, 1.1, "TRACKS_KEYWORDS")
diamond(ax, 8.9, 9.4, 2.6, 1.1, "HAS_PREFERENCES")
diamond(ax, 8.9, 8.1, 2.2, 1.1, "FOLLOWS")
diamond(ax, 8.9, 6.0, 2.3, 1.1, "RECEIVES")
diamond(ax, 13.0, 7.1, 2.2, 1.1, "BOOKMARKS")
diamond(ax, 13.0, 4.9, 2.0, 1.1, "REACTS")
diamond(ax, 20.2, 10.9, 2.6, 1.1, "TRANSFORMED_TO")

# Relationship edges with cardinalities
edge(ax, 4.0, 8.9, 4.2, 9.2, "1")
edge(ax, 6.3, 9.6, 6.0, 10.0, "1")
edge(ax, 4.0, 8.2, 4.2, 7.3, "1")
edge(ax, 6.3, 6.8, 6.0, 6.7, "0..1")
edge(ax, 4.4, 8.8, 7.6, 9.4, "1")
edge(ax, 10.2, 9.5, 9.6, 10.0, "0..1")
edge(ax, 4.4, 8.4, 7.7, 8.2, "1")
edge(ax, 10.0, 8.0, 9.5, 8.1, "0..N")
edge(ax, 4.1, 7.9, 7.6, 6.2, "1")
edge(ax, 10.0, 6.0, 9.4, 6.0, "0..N")
edge(ax, 12.0, 7.1, 12.0, 7.1, "1")
edge(ax, 14.0, 7.1, 13.7, 7.1, "0..N")
edge(ax, 12.0, 4.9, 12.0, 4.9, "1")
edge(ax, 14.0, 4.9, 13.7, 4.9, "0..N")
edge(ax, 19.4, 8.9, 19.6, 10.4, "1")
edge(ax, 20.8, 10.4, 21.1, 8.9, "0..1")

plt.tight_layout()
plt.savefig("ERD_chen_landscape.png", dpi=220, bbox_inches="tight", facecolor="white")
