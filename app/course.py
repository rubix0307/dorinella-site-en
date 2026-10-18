"""«Gut Massage» — the web version of the abdominal self-massage course.

Admin-only for now: nothing public links here (not the catalog, the nav, the footer, the API),
see app/routers/course.py for the gate and `access_mode()` for the switch to «paid» / «public» later.

The content is data, like app/learn.py: a Course has Pages in Groups, a Page has Blocks, a Block's `kind` picks
templates/includes/course/<kind>.html. The 8 techniques of the animated player live in course_data/massage.json;
the step lists on the page are rendered from the very same captions the player shows (one source, no drift).

Wording is deliberately cautious: it never promises a medical result and points to a doctor wherever a condition could matter.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import safety, site
from .core.config import settings

log = logging.getLogger(__name__)

SLUG = 'abdominal-massage'
PRODUCT = 'massage-course'      # the catalog product that sells it (the entitlement to check)
BASE = f'/course/{SLUG}/'
DATA_URL = f'{BASE}data/massage.json'
DATA_FILE = Path(__file__).parent / 'course_data' / 'massage.json'
PHOTO = 'img/course/belly-896.webp'   # the belly photo of the player (static, shipped with the code)
SAFETY_VERSION = '2026-10-18'     # bump when the safety copy changes: everyone is asked to confirm it again


# ---------------------------------------------------------------- access

def access_mode() -> str:
    """admin (default) | paid | public. One function, so tests can patch it (settings is frozen)."""
    mode = settings.course_access
    return mode if mode in ('admin', 'paid', 'public') else 'admin'


def is_staff(user: Any) -> bool:
    """Admins only: a signed-in account with role «user» (a plain customer) is not staff."""
    return getattr(user, 'role', '') in ('root', 'admin')


def listed_for(user: Any) -> bool:
    """Whether links to the course may be shown to this account (the cabinet, the admin menu)."""
    return access_mode() == 'public' or (user is not None and is_staff(user))


# ---------------------------------------------------------------- structure

@dataclass(frozen=True)
class Block:
    kind: str
    d: dict


def B(kind: str, **data) -> Block:  # noqa: N802 — the short name of the content DSL
    return Block(kind, data)


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    nav: str                 # short label in the chapter list
    group: int
    promise: str             # one line: what this page gives
    blocks: tuple[Block, ...]


GROUPS = ('Start', 'Safety', 'Preparation', 'Techniques', 'Food and answers')


# ---------------------------------------------------------------- the techniques (player data)

def _tech_extra() -> dict[str, dict]:
    return {
        'm1': {'lead': 'Strokes in the shapes of the letters I, L and U, following the path of the colon.',
               'gives': ['May support the natural movement of the gut', 'May ease a bloated, uncomfortable feeling']},
        'm2': {'lead': 'Boosts circulation and may ease digestion after a meal.',
               'gives': ['Warms up the belly muscles', 'A good start for any massage']},
        'm3': {'lead': 'A good fit for constipation or mild cramps (never sharp pain).',
               'gives': ['May help food move along', 'May ease cramping']},
        'm4': {'lead': 'Breathing and light massage combined for relaxation.',
               'gives': ['Relaxes the belly muscles', 'Helps you calm down']},
        'm5': {'lead': 'Mimics the natural movement of the intestines.',
               'gives': ['May support the natural movement of the gut', 'A smooth, rhythmic stroke']},
        'm6': {'lead': 'Boosts circulation across the whole belly and helps you notice areas that need more attention.',
               'gives': ['Covers the whole belly', 'Helps you notice tense spots']},
        'm7': {'lead': 'Mimics the natural movement of the intestines; a good fit when you feel sluggish or heavy.',
               'gives': ['May support the natural movement of the gut', 'May ease a heavy feeling']},
        'm8': {'lead': 'Works different areas of the belly and helps you feel balance and symmetry.',
               'gives': ['Works the whole belly evenly', 'Gives you a clear order of moves']},
    }


def _seconds(t: float) -> str:
    return f'{int(t // 60)}:{int(t % 60):02d}'


def load_techniques() -> list[dict]:
    """The 8 techniques for the page: title, lead, steps (label, text, start second), closing note, what it gives."""
    raw = json.loads(DATA_FILE.read_text(encoding='utf-8'))['techniques']
    extra = _tech_extra()
    out = []
    for number, (key, t) in enumerate(raw.items(), 1):
        steps = [(c[1], c[2], c[0]) for c in t['captions'] if c[1]]
        closing = next((c[2] for c in t['captions'] if not c[1] and c[0] > 0), '')
        lead = next((c[2] for c in t['captions'] if not c[1] and c[0] == 0), extra[key]['lead'])
        out.append({'key': key, 'number': number, 'name': t['short'], 'title': t['title'], 'total': t['total'],
                    'time': _seconds(t['total']), 'lead': lead, 'steps': steps, 'closing': closing,
                    'gives': extra[key]['gives']})
    return out


TECHNIQUES = load_techniques()
TECH_BY_KEY = {t['key']: t for t in TECHNIQUES}


# ---------------------------------------------------------------- pages

OILS_NOTE = ('Essential oils are optional. Always dilute them in a carrier oil (1–2 drops per tablespoon, about 15 ml) '
             'and patch-test on a small area of skin first. Skip them during pregnancy, with allergies, asthma, epilepsy or high blood pressure, '
             'and for children unless your doctor says otherwise.')

PAGES: tuple[Page, ...] = (
    Page('about', 'About the course and your “second brain”', 'About', 0,
         'What to expect and why your gut matters for how you feel.',
         (
             B('text', paras=(
                 'Your gut does far more than digest food. This course helps you get to know your body and support it with gentle, natural habits: '
                 'step-by-step massage techniques, practical tips and simple tools.',
             )),
             B('facts', title='More than a digestive organ', intro='Trillions of bacteria live in your gut. It is linked to:',
               rows=(
                   ('Motility', 'The movement of food through the digestive tract.'),
                   ('The immune system', 'A large share of immune cells is connected to the gut.'),
                   ('Serotonin', 'A large share of the “feel-good” chemical serotonin is made in the gut.'),
               )),
             B('callout', tone='info', title='Ever noticed?',
               text='After a heavy meal you may feel more than bloating: irritable, tired. Your gut and brain are in constant conversation '
                    'through the nervous system, so digestive trouble (constipation, bloating, irregularity) can show up in your mood, energy and even your skin.'),
             B('facts', title='What belly massage may do', cols=3, rows=(
                 ('Circulation', 'Gentle massage may boost blood flow to the belly, so the tissues get more oxygen and nutrients.'),
                 ('Motility', 'Light pressure and stroking may help the gut contract and food move along.'),
                 ('Less tension', 'The belly is where many of us hold stress. A soft massage helps muscles and nerves relax.'),
             )),
             B('callout', tone='tip', title='How to use the course',
               text='Start with the “Safety” page, then read about preparation and move on to the techniques. '
                    'You may feel a difference after the first session (a bit less bloating or tension, for example), '
                    'but a lasting effect usually takes regular practice over several weeks. Results vary from person to person.'),
         )),

    Page('safety', 'Safety: can you massage your belly?', 'Safety', 1,
         'Check the contraindications before you start. It takes two minutes.',
         (
             B('text', paras=(
                 'Belly massage can be a pleasant, gentle habit, but there are situations where you should be careful or skip it altogether. '
                 'Below is a list of contraindications with explanations. It is general information, not medical advice.',
             )),
             B('safety', version=SAFETY_VERSION, date='October 18, 2026',
               absolute_title='Do not massage',
               absolute_lead='If any one of these applies to you, do not massage your belly and talk to your doctor.',
               absolute=(
                   ('a1', 'Sudden or severe abdominal pain', 'For example, suspected appendicitis, pancreatitis, a perforation or a bowel obstruction. '
                                                             'Massage can make inflammation worse. See a doctor right away for a diagnosis.'),
                   ('a2', 'Tumors or cancer in the abdomen', 'Massage increases circulation, which is not advised with these conditions. Ask your care team first.'),
                   ('a3', 'Pregnancy (or a chance you could be pregnant)', 'Do not massage the belly during pregnancy unless your doctor or midwife has said it is fine. '
                                                                         'If pregnancy is possible, find out first.'),
                   ('a4', 'Recent abdominal surgery', 'For example, an appendectomy or a C-section, or a recent colonoscopy: no massage until you have fully healed '
                                                      'and your doctor has cleared you.'),
                   ('a5', 'An acute infection', 'A stomach bug or food poisoning with diarrhea, vomiting or fever.'),
                   ('a6', 'A hernia', 'Pressing on the belly can make a hernia worse. Skip massage until a doctor has checked it.'),
                   ('a7', 'Diverticulitis or an abdominal aortic aneurysm', 'Pressure on an inflamed or weakened part of the gut or a major vessel can be dangerous. Ask your doctor first.'),
               ),
               relative_title='Be careful and talk to your doctor',
               relative_lead='If any of these applies to you, talk to your doctor about massage first and listen to your body.',
               relative=(
                   ('r1', 'Your period', 'Massage may make bleeding heavier. It is better to leave the lower belly alone; if you feel any discomfort, skip it.'),
                   ('r2', 'A chronic bowel condition', 'Crohn’s disease or ulcerative colitis: during a flare-up, any handling of the belly '
                                                       'can be painful and harmful. In remission, only after talking to your doctor.'),
                   ('r3', 'Gallstones', 'Massage might shift stones, which risks a gallbladder attack. Be sure to talk to your doctor.'),
                   ('r4', 'Irritable bowel syndrome (IBS)', 'Talk to your doctor. Usually only soft techniques without firm pressure are suitable.'),
                   ('r5', 'Blood-thinning medication or a blood-clotting problem', 'Pressing on the belly may cause bruising or bleeding. Ask your doctor before you try anything.'),
               )),
             B('callout', tone='info', title='Go by how you feel',
               rows=('Massage should be gentle and never painful.',
                     'Start with short sessions and notice how you feel.',
                     'When in doubt, always ask your doctor.')),
         )),

    Page('body', 'Your body: the gut and your breath', 'Body', 2,
         'What is where, and how to breathe so your belly relaxes.',
         (
             B('anatomy'),
             B('facts', title='Key spots for massage', cols=3, rows=(
                 ('2 · Cecum', 'The lower right corner of your belly, where the large intestine begins.'),
                 ('4 · Transverse colon', 'Above the navel, where gas can collect.'),
                 ('6 · Sigmoid colon', 'The lower left corner, the last bend before the rectum, where stool waits to pass.'),
             )),
             B('callout', tone='tip', title='Did you know?',
               rows=('The surface area of the gut is often compared to that of a tennis court.',
                     'Trillions of bacteria live in it and are linked to digestion, immunity and even mood.')),
             B('facts', title='Breathing and your gut',
               intro='Breathing reaches the gut through the vagus nerve, the main “line of communication” between gut and brain. '
                     'Calm breathing and gentle massage can help you relax. It is a supportive practice, not a treatment.',
               cols=3, rows=(
                   ('Relaxes the belly muscles', 'Deep breathing lowers tension in the muscles of the abdomen.'),
                   ('Eases stress', 'Slow breathing activates the part of the nervous system that handles rest and recovery.'),
                   ('Supports motility', 'Deep breaths in and out gently stir the gut and may ease bloating and discomfort.'),
               )),
             B('steps', title='Diaphragmatic breathing', intro='One of the simplest ways to release tension in your belly. You can do it every day.',
               rows=(
                   ('Get comfortable', 'Lie on your back with your knees bent. Place one hand on your chest and the other on your belly.'),
                   ('Inhale', 'Breathe in slowly through your nose so your belly rises, not your chest.'),
                   ('Exhale', 'Breathe out slowly and feel your belly sink.'),
               ),
               link=('techniques', 'm4', 'The same thing together with massage: technique #4')),
         )),

    Page('preparation', 'Getting ready to massage', 'Preparation', 2,
         'When, on what and with what to massage.',
         (
             B('text', paras=('A little preparation helps your muscles relax, improves circulation and makes the massage more comfortable.',)),
             B('facts', title='The best time', cols=2, rows=(
                 ('In the morning, before eating', 'Helps to “wake up” your digestion.'),
                 ('In the evening, before bed', 'Helps you unwind and supports your gut during rest.'),
             )),
             B('callout', tone='warn', title='Not right after a meal',
               text='Massaging right after eating is not recommended: it can cause discomfort, bloating or slow digestion. '
                    'It is best to wait 1.5–2 hours after a meal, when your stomach is not full.'),
             B('steps', title='Preparing your body', rows=(
                 ('A warm compress (optional)', 'Soak a soft towel in warm water, wring it out and rest it on your belly for 5–10 minutes. '
                                                'It warms the muscles, boosts circulation and eases tension.'),
                 ('A glass of warm water', 'It is nice to drink one 20–30 minutes before the massage.'),
             )),
             B('facts', title='Massage oils (optional)', cols=2, rows=(
                 ('A carrier oil', 'Almond or coconut oil, to moisturize the skin and help your hands glide.'),
                 ('With essential oils', 'Rosemary, peppermint or lavender, for relaxation and a pleasant scent.'),
             )),
             B('callout', tone='warn', title='About essential oils', text=OILS_NOTE),
             B('steps', title='The right position', rows=(
                 ('Lie down', 'On your back on a flat surface.'),
                 ('Legs', 'Bend your knees and lift them slightly, for example by resting your legs on a pillow.'),
                 ('Hands and belly', 'Keep your hands relaxed and your belly soft.'),
             )),
         )),

    Page('techniques', '8 massage techniques', 'Techniques', 3,
         'Watch, follow the steps, and pick your technique.',
         (B('hub'),)),

    Page('combining', 'Combining techniques', 'Combining', 3,
         'A ready-made sequence, and how to listen to yourself.',
         (
             B('text', paras=('You can mix and match any of the techniques to suit how you feel and what you need.',)),
             B('sequence', title='An example sequence', rows=(
                 ('m2', 'Start with circular motions', 'To warm up.'),
                 ('m1', 'Move on to “I Love You”', 'Strokes that follow the path of the colon.'),
                 ('m3', 'Finish with light pressure on the points', 'Gently, with no pain.'),
             ), note='Each animation shows one cycle of the movement. Repeat it as many times as the technique says, '
                  'or as many times as feels comfortable if no number is given, and stop if you feel pain.'),
             B('facts', title='Listen to your body', cols=3, rows=(
                 ('Day by day', 'The more you practice, the more your body starts to “talk” to you.'),
                 ('Intuitively', 'You will sense which moves you need right now and where there is tension or sluggishness.'),
                 ('A way to know yourself', 'It is more than a technique: it is a way to be more attentive to yourself. Trust what you feel.'),
             )),
         )),

    Page('goals', 'Massage for your goal', 'Your goal', 3,
         'Bloating, constipation, relaxation, anxiety: what to choose.',
         (B('goals'),)),

    Page('food', 'Food for your gut', 'Food', 4,
         'Fiber, prebiotics and probiotics in one place.',
         (B('foodlists'),)),

    Page('water', 'Water and drinks', 'Water and drinks', 4,
         'How much to drink, and six simple drinks with a timer.',
         (B('water'), B('drinks'))),

    Page('faq', 'Frequently asked questions', 'FAQ', 4,
         'Answers about how often, pain, periods and results.',
         (B('faq'),)),

    Page('author', 'About the author', 'Author', 4,
         'Who created the course.',
         (B('author'),)),
)

PAGE_BY_SLUG = {p.slug: p for p in PAGES}
PUBLIC_PAGES = PAGES


def neighbours(page: Page) -> tuple[Page | None, Page | None]:
    """Previous and next page in reading order (the sign-off page is outside the line)."""
    line = list(PUBLIC_PAGES)
    if page not in line:
        return None, None
    i = line.index(page)
    return (line[i - 1] if i else None), (line[i + 1] if i + 1 < len(line) else None)


# ---------------------------------------------------------------- shared data of the interactive blocks

ANATOMY = (
    (1, 'Small intestine', 'Three sections: the duodenum, the jejunum and the ileum. It breaks down food and absorbs nutrients into the blood. '
                           'It sits in the center of the belly, framed by the large intestine.', 49, 37, 'r'),
    (2, 'Cecum', 'The start of the large intestine, in the lower right of the belly. Food arrives here from the small intestine '
                 '(through the ileocecal valve). The appendix branches off here.', 30, 51, 'l'),
    (3, 'Ascending colon', 'Runs up the right side of the belly to the right bend (the hepatic flexure) under the ribs.', 28, 35, 'l'),
    (4, 'Transverse colon', 'Runs across the belly from the right bend to the left. The left bend (the splenic flexure) sits higher than the right, '
                            'and the colon itself can sag below the navel.', 47, 26, 'l'),
    (5, 'Descending colon', 'Runs down the left side of the belly. Stool becomes firmer here and waits before it leaves the body.', 74, 26, 'r'),
    (6, 'Sigmoid colon', 'An S-shaped loop in the lower left of the belly, the second-to-last section before the rectum.', 66, 64, 'r'),
    (7, 'Rectum', 'The final section of the large intestine, ending at the anal canal.', 53, 70, 'l'),
)

GOALS = (
    {'id': 'bloating', 'title': 'Bloating', 'lead': 'When your belly feels puffy and heavy.',
     'prep': ('A warm compress on your belly before the massage helps the muscles relax and may help gas pass.',
              'If you like: 1–2 drops of peppermint or fennel essential oil per tablespoon of carrier oil (almond or coconut).'),
     'order': 'Light circles clockwise, starting at the lower right of the belly → “Spiral” → gentle pressure on the areas where you feel the most discomfort.',
     'chips': (('m2', 'Circles'), ('m6', 'Spiral'), ('m3', 'Pressure points')),
     'tips': ('Afterwards, sip a warm cup of fennel, peppermint or ginger tea.',
              'Avoid a heavy meal before the massage, especially foods that tend to cause gas for you (for example cabbage or beans).')},
    {'id': 'constipation', 'title': 'Constipation', 'lead': 'When your gut feels sluggish.',
     'prep': ('A glass of warm water with lemon 20–30 minutes before the massage.',
              'If you like: a carrier oil with a few drops of orange or rosemary essential oil.'),
     'order': '“I Love You” → light pressure on the area just below the navel (the small intestine zone) → finish with the “Wave” to encourage food to move in the right direction.',
     'chips': (('m1', 'I Love You'), ('m3', 'Pressure points'), ('m5', 'Wave')),
     'tips': ('Afterwards, try a few forward bends or gentle hip circles.',
              'Eat more fiber (vegetables, fruit, chia seeds) and drink enough water. Constipation is often linked to too little fiber, too little water or stress. '
              'If it lasts, talk to your doctor.')},
    {'id': 'relax', 'title': 'Relaxation', 'lead': 'Tension or ongoing tiredness.',
     'prep': ('Put on calm music or nature sounds.',
              'If you like: a carrier oil with lavender or chamomile.'),
     'order': 'Light circles over the whole belly, clockwise → the “Square” for an even, relaxing pass → finish with your hands resting on your belly '
              'and a few deep breaths in through the nose and out through the mouth.',
     'chips': (('m2', 'Circles'), ('m8', 'Square'), ('m4', 'Breathing')),
     'tips': ('Afterwards, enjoy a cup of chamomile or lemon balm tea.',
              'Try the massage in the evening, before bed.')},
    {'id': 'anxiety', 'title': 'Anxiety', 'lead': 'When stress “clenches” your belly.',
     'prep': ('A warm compress; if you like, a few drops of neroli or ylang-ylang essential oil.',
              'Before you start, take 1–2 minutes of diaphragmatic breathing to settle your nervous system.'),
     'order': 'Slow circles over the whole belly, clockwise → light pressure just above the navel to ease tension → finish with the “Wave,” from top to bottom.',
     'chips': (('m4', 'Breathing'), ('m2', 'Circles'), ('m5', 'Wave')),
     'tips': ('Afterwards, a warm bath with lavender can be a nice way to unwind.',
              'Persistent anxiety deserves proper support. Massage is a calming habit, not a treatment, so talk to a professional if it affects your daily life.')},
)

FOOD_LISTS = (
    {'id': 'fibre', 'title': 'Sources of fiber',
     'lead': 'Fiber is food for your friendly gut bacteria and helps keep things moving. Add it gradually and drink plenty of water with it.',
     'groups': (
         ('Vegetables', ('Broccoli', 'Cauliflower', 'Carrots', 'Zucchini', 'Spinach', 'Pumpkin or butternut squash')),
         ('Fruit', ('Apples with the skin on', 'Pears', 'Berries (blueberries, raspberries)', 'Prunes')),
         ('Whole grains', ('Oatmeal', 'Brown rice', 'Buckwheat', 'Whole-grain rye or whole-wheat bread')),
         ('Seeds', ('Flaxseed (1–2 tsp, ground or soaked in water)', 'Chia seeds (soaked in water or a drink)')),
         ('Legumes', ('Chickpeas', 'Beans', 'Lentils')),
     ),
     'tips': ('Start your day with oatmeal topped with seeds and berries.',
              'Add a serving of vegetables to every meal (steamed broccoli or roasted squash, for example).',
              'Cook beans with a spice such as cumin, and rinse canned beans well. Some people find this makes them easier to digest.')},
    {'id': 'pro', 'title': 'Probiotics',
     'lead': 'Live friendly bacteria that support a healthy balance in the gut. They are found in fermented foods.',
     'groups': (
         ('Fermented foods', ('Kefir or yogurt with live cultures (unsweetened)', 'Sauerkraut (refrigerated, unpasteurized)',
                              'Kimchi (a spicy Korean dish of fermented vegetables)', 'Miso (a Japanese paste made from fermented soybeans)',
                              'Kombucha (fermented tea), in small amounts')),
     ),
     'tips': ('Try a little kefir in the evening, if it sits well with you.',
              'Serve a spoonful of sauerkraut as a side with your main dish.')},
    {'id': 'pre', 'title': 'Prebiotics',
     'lead': 'Plant fibers that “feed” your friendly bacteria and help them grow.',
     'groups': (
         ('Vegetables', ('Onions', 'Garlic', 'Asparagus', 'Artichokes', 'Jerusalem artichokes (sunchokes)')),
         ('Fruit', ('Bananas (slightly underripe)', 'Apples')),
         ('Whole grains', ('Barley', 'Oats')),
         ('Other', ('Flaxseed', 'Chicory root')),
     ),
     'tips': ('Cook with onions and garlic. They are natural prebiotics.',
              'Add Jerusalem artichokes to salads or roast them as a side.',
              'Prebiotic foods can cause gas at first, so introduce them slowly. If you have IBS or a sensitive gut, ask your doctor or a dietitian.')},
)

WATER = {
    'lead': 'Staying hydrated matters for a well-working gut: without enough water, fiber can make constipation worse.',
    'best': 'A good choice is a warm herbal tea, such as fennel or peppermint, to help you unwind.',
    'tips': ('Aim for roughly 6–8 cups (1.5–2 liters) of fluids a day, more if you are active or it is hot. If your doctor limits your fluids, follow their advice.',
             'Start the day with a glass of warm water, with lemon if you like, to wake up your digestion.',
             'Sip warm water in small amounts through the day.'),
}

DRINKS = (
    {'id': 'lemon', 'title': 'Lemon water', 'minutes': 0,
     'for': ('A gentle start if your digestion feels slow.', 'A simple morning ritual to wake up your system.'),
     'how': ('Squeeze half a lemon into a glass of warm (not hot) water.', 'Drink it on an empty stomach, 20–30 minutes before breakfast.'),
     'helps': ('Warm liquid in the morning may help get your gut going.', 'Many people find it a refreshing, easy habit.'),
     'care': 'If you have gallstones, acid reflux or sensitive teeth, ask your doctor first and rinse your mouth with plain water afterwards.'},
    {'id': 'ginger', 'title': 'Ginger tea', 'minutes': 10,
     'for': ('If you often feel bloated or queasy, or digest heavy meals slowly.',),
     'how': ('Slice about 1 inch (2–3 cm) of fresh ginger root and pour boiling water over it.', 'Let it steep for 5–10 minutes.', 'Add a teaspoon of honey or a squeeze of lemon if you like.'),
     'helps': ('Ginger is traditionally used to settle the stomach.', 'May ease bloating and a heavy feeling.'),
     'care': 'Ginger may not suit you if you take blood thinners or have gallstones. Ask your doctor if you are unsure.'},
    {'id': 'fennel', 'title': 'Fennel tea', 'minutes': 10,
     'for': ('If you have bloating or gas, or simply want to calm your gut.',),
     'how': ('Pour 1 cup (about 240 ml) of boiling water over 1 tsp of fennel seeds.', 'Steep for 10 minutes and strain.'),
     'helps': ('Traditionally used to ease gas and cramping.', 'A warm, soothing cup after a meal.'),
     'care': 'Skip it if you are allergic to plants in the carrot family, and ask your doctor first during pregnancy.'},
    {'id': 'golden', 'title': 'Golden milk (turmeric)', 'minutes': 0,
     'for': ('A traditional evening drink for anyone who wants a cozy, calming ritual.',),
     'how': ('Warm a glass of milk (dairy or plant-based) and stir in ½ tsp turmeric powder, a pinch of black pepper and a little honey.', 'Mix well and drink before bed.'),
     'helps': ('Traditionally enjoyed to support digestion.',),
     'care': 'Turmeric may not suit you with gallstones, blood-thinning medication or during pregnancy. Ask your doctor if you are unsure. It is a drink, not a treatment.'},
    {'id': 'mint', 'title': 'Peppermint tea', 'minutes': 10,
     'for': ('If you feel stressed or have occasional cramps.',),
     'how': ('Steep 1 tsp of dried peppermint and 1 tsp of lemon balm in a cup of boiling water.', 'Let it steep for 10 minutes.'),
     'helps': ('A calming cup that many people find relaxing.', 'May ease bloating and cramping.'),
     'care': 'Peppermint can aggravate acid reflux or heartburn. If that happens to you, choose a different tea.'},
    {'id': 'flax', 'title': 'Flaxseed water', 'minutes': 0,
     'for': ('If you are prone to constipation or want something soothing for your gut.',),
     'how': ('Pour 1 cup (about 240 ml) of boiling water over 1 tbsp of flaxseed, cover, and leave it overnight.', 'In the morning, drink the gel-like liquid.'),
     'helps': ('Flaxseed is rich in fiber and may support regularity.', 'Its gel-like texture is gentle and coating.'),
     'care': 'Drink plenty of water with flaxseed and take it at least 1–2 hours apart from any medication. Not for people with a bowel obstruction or trouble swallowing.'},
)

FAQ = (
    ('Safety', (
        ('Can I do the massage during my period?',
         ('It is best to avoid intense massage during your period, especially on the lower belly, so that you do not make bleeding heavier. '
          'A light massage of the upper belly or some diaphragmatic breathing may ease cramps and help you feel better overall.',)),
        ('Does massage help with irritable bowel syndrome (IBS)?',
         ('A light massage may help some people feel less cramping and bloating. Try the “Spiral” or “I Love You” '
          'for a soft touch, and avoid firm pressure. Talk to your doctor first.',)),
        ('Can I do it if I have a chronic bowel condition?',
         ('With a condition such as Crohn’s disease or ulcerative colitis, massage is only an option during remission and with your doctor’s approval. '
          'Stick to light techniques that ease tension, and avoid anything intense.',)),
        ('What if I feel discomfort or pain during the massage?',
         ('If you feel sharp pain, stop right away and talk to your doctor. Mild discomfort can happen, '
          'but you should adapt the technique by easing off the pressure.',)),
    )),
    ('Practice', (
        ('How often can I do the massage?',
         ('Many people do it once or twice a day: in the morning on an empty stomach to wake up digestion, and in the evening before bed to unwind. '
          'If you feel discomfort or your body needs more time, cut back to a few times a week.',)),
        ('Do I need to prepare?',
         ('It helps: drink a glass of warm water 20–30 minutes before, rest a warm compress on your belly to relax the muscles, '
          'and choose a comfortable position (lying on your back with your knees bent).',)),
        ('Can I massage right after eating?',
         ('No, massaging right after a meal is not recommended: it can cause discomfort, bloating or slow digestion. '
          'It is best to wait 1.5–2 hours after eating, when your food is partly digested and your stomach is not full.',)),
        ('Do I have to use oil?',
         ('No, oil is optional, but it helps your hands glide and adds a relaxing touch. Choose a carrier oil (almond or coconut), '
          'with an essential oil (peppermint, lavender, rosemary) added if you like. ' + OILS_NOTE,)),
        ('Can I combine the massage with stomach vacuum exercises?',
         ('Some people do. Stomach vacuum exercises can go well with massage because they work the diaphragm and the belly muscles. '
          'Do them after the massage or separately, never on a full stomach, and skip them with a contraindication (pregnancy, recent surgery, a hernia). If in doubt, ask your doctor.',)),
    )),
    ('Expectations', (
        ('How soon will I see results?',
         ('Some people feel a difference after the first session, such as less bloating or tension. '
          'For a lasting effect, regular practice over several weeks usually works best. Everyone is different.',)),
        ('Does gut massage help with weight loss?',
         ('No. Massage does not burn fat. It may help you feel more comfortable after eating, which can support a healthy routine. '
          'For the best results, combine it with balanced eating and regular movement.',)),
    )),
)

STOP_RULES = safety.STOP_SIGNS

# TODO(owner): replace this placeholder with the real bio of the author; nothing below is a claim about her background.
AUTHOR = {
    'name': site.AUTHOR_NAME,
    'paras': (f'This course was created by {site.AUTHOR_NAME}, {site.AUTHOR_JOB.lower()}. It shares the gentle gut-care habits she teaches.',
              'Small, steady steps are what count, and this course is meant to be a calm, practical companion along the way.'),
    'motto': 'Small steps, taken gently, add up.',
}
