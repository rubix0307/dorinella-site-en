"""The safety lists the guide, the course and the Terms all print, so that they cannot disagree about who should skip abdominal massage or when to see a doctor.
Written for a lay reader; the course's tick-box page (app/course.py, 'safety') splits the same conditions into "do not" and "ask first"."""

# Skip abdominal massage, and talk with a doctor first, if any of these applies.
SKIP_CONDITIONS = (
    'pregnancy, or a chance you could be pregnant',
    'recent abdominal surgery or a recent colonoscopy',
    'a hernia',
    'an inflammatory bowel disease flare-up (Crohn’s disease or ulcerative colitis)',
    'diverticulitis',
    'an abdominal aortic aneurysm',
    'a known or suspected abdominal tumor',
    'a suspected bowel obstruction',
    'an acute stomach infection with diarrhea, vomiting or fever',
    'belly pain, fever or blood in your stool that has not been explained',
)

# Ask a doctor before you start (a milder rule than skipping).
ASK_FIRST = ('gallstones', 'irritable bowel syndrome', 'blood-thinning medication or a blood-clotting problem')

# Stop at once and get medical help if any of these shows up, while practicing or at any other time.
STOP_SIGNS = (
    'sharp, severe or worsening pain',
    'nausea, vomiting or dizziness',
    'blood in or on your stool, or black, tar-like stool',
    'fever',
    'unexplained weight loss',
    'no stool or gas passing together with a swollen, painful belly',
    'a sudden change for the worse in how you feel',
)


def sentence(items: tuple[str, ...]) -> str:
    """'a, b or c'; with semicolons ('a; b; or c') when an item holds a comma or an "or" itself, so that the items cannot run together."""
    if len(items) > 1:
        nested = any(',' in item or ' or ' in item for item in items)
        return ('; ' if nested else ', ').join(items[:-1]) + ('; or ' if nested else ' or ') + items[-1]
    return items[0] if items else ''
