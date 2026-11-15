"""The guide library and the Bristol stool scale test. The text is health education, not medical advice: it never diagnoses."""
from dataclasses import dataclass

from . import safety

# ---------------------------------------------------------------- test


@dataclass(frozen=True)
class Question:
    text: str
    options: tuple[tuple[str, int], ...]   # (label, points)
    label: str = ''                        # short heading above the text
    notes: tuple[str, ...] = ()            # per option: a sentence added to the result when it is chosen ('' for none)
    alert: bool = False                    # the notes are a warning (shown as one), not a remark
    # (note index, answers of the FIRST question): the note does not fit when the first question had one of these answers
    # (constipation wording next to watery stool)
    note_unless_first: tuple[tuple[int, tuple[int, ...]], ...] = ()


@dataclass(frozen=True)
class Band:
    """Result of a single-score test: the first band whose min_score the score reaches."""
    key: str
    min_score: int
    title: str
    text: str
    tips: tuple[str, ...] = ()
    doctor: str = ''                # when to see a doctor, specific to this result
    massage: bool = False           # whether gentle abdominal self-massage is a reasonable thing to mention


@dataclass(frozen=True)
class SelfTest:
    slug: str
    title: str
    tagline: str
    intro: str
    questions: tuple[Question, ...]
    minutes: int
    theme: str = ''
    bands: tuple[Band, ...] = ()       # highest min_score first
    note: str = ''
    guide_slug: str | None = None      # the free guide the result points to
    paid_guide_slug: str | None = None
    course_slug: str | None = None     # a product slug

    @property
    def max_score(self) -> int:
        return sum(max(points for _, points in q.options) for q in self.questions)

    def score(self, answers: list[int]) -> int:
        return sum(q.options[a][1] for q, a in zip(self.questions, answers, strict=False))

    def band_for(self, score: int) -> Band:
        return next((b for b in self.bands if score >= b.min_score), self.bands[-1])

    def notes_for(self, answers: list[int]) -> list[tuple[str, bool]]:
        """(sentence, is_warning) for every chosen option that carries a note."""
        first = answers[0] if answers else -1
        return [(q.notes[a], q.alert) for q, a in zip(self.questions, answers, strict=False)
                if q.notes and q.notes[a] and not any(a == i and first in firsts for i, firsts in q.note_unless_first)]

    def alerted(self, answers: list[int]) -> bool:
        """Whether a warning-type answer was chosen."""
        return any(q.alert and q.notes and q.notes[a] for q, a in zip(self.questions, answers, strict=False))

    def validate(self, answers: list[int]) -> list[str]:
        """Problems with a submitted answer list (empty when it is valid)."""
        if len(answers) != len(self.questions):
            return [f'expected {len(self.questions)} answers, got {len(answers)}']
        return [f'answer {i}: option {a} does not exist' for i, (q, a) in enumerate(zip(self.questions, answers, strict=False))
                if not 0 <= a < len(q.options)]


_TYPE_LABELS = (
    'Type 1: separate hard lumps, like nuts, hard to pass',
    'Type 2: sausage-shaped, but lumpy',
    'Type 3: like a sausage, with cracks on the surface',
    'Type 4: like a sausage or snake, smooth and soft',
    'Type 5: soft blobs with clear-cut edges',
    'Type 6: fluffy, mushy pieces with ragged edges',
    'Type 7: watery, no solid pieces',
)

_DOCTOR_ANY = ('Blood in or on the stool, black or tar-like stool, unexplained weight loss, severe or lasting abdominal pain, '
               'or a clear change from your usual pattern that lasts more than a few weeks are always worth a visit to a doctor.')

BRISTOL = SelfTest(
    slug='bristol',
    title='Bristol Stool Scale Test',
    tagline='Three quick questions, about a minute, and a plain-language reading of what your stool shape may be telling you',
    intro=('The Bristol Stool Scale is a simple chart doctors use to describe stool shape, which reflects how quickly food moves through the gut. '
           'Answer for what is usual for you over the past few weeks. There are no right or wrong answers, and nothing here is a diagnosis.'),
    minutes=1,
    theme='theme-guts',
    questions=(
        Question('Which description fits your stool on most days?', tuple((label, n) for n, label in enumerate(_TYPE_LABELS, 1)),
                 label='Shape'),
        Question('How often do you usually have a bowel movement?',
                 (('More than three times a day', 0), ('One to three times a day', 0), ('Three to six times a week', 0),
                  ('Fewer than three times a week', 0), ('It changes a lot from week to week', 0)),
                 label='Frequency', note_unless_first=((3, (5, 6)),),
                 notes=('Going more than three times a day is normal for some people. If it is new for you, or comes with loose stools or urgency, '
                        'mention it to a doctor.',
                        '',
                        'That is within the usual range.',
                        'Fewer than three bowel movements a week is how constipation is commonly defined. If this is your pattern and it bothers you, '
                        'talk with a doctor or pharmacist.',
                        'Swings between hard and loose stools are worth tracking for a couple of weeks. Bring your notes to a doctor if they keep happening.')),
        Question('In the past month, have you noticed any of these: blood in or on the stool, black or tar-like stool, unexplained weight loss, '
                 'severe or lasting abdominal pain, or diarrhea that wakes you at night?',
                 (('No, none of these', 0), ('Yes, one or more', 0), ('I am not sure', 0)),
                 label='Warning signs', alert=True,
                 notes=('',
                        'Those signs should be checked by a doctor. Please book an appointment soon rather than waiting. If the pain is severe, '
                        'you are vomiting, feel faint or are passing a lot of blood, seek urgent care. Self-help and massage can wait until you have been seen.',
                        'If you are not sure whether what you noticed counts, treat it as a warning sign for now and check with a doctor or pharmacist before anything else. '
                        'Asking costs nothing.')),
    ),
    bands=(
        Band('type-7', 7, 'Type 7: watery stool',
             'Completely liquid stool means food is moving through very quickly. It is the pattern of diarrhea. Most short bouts come from a stomach bug, '
             'something you ate, a medicine or stress, and settle within a couple of days.\n\n'
             'The main risk is losing too much fluid. Rest, and replace what you lose.',
             ('Sip fluids all day. An oral rehydration solution from a pharmacy works better than plain water because it also replaces salts.',
              'Eat small, plain meals when you are hungry (rice, bananas, toast, soup) and skip alcohol, very sugary drinks and greasy food for now.',
              'Wash your hands often; a gut infection can spread to others.'),
             'See a doctor if diarrhea lasts more than two days, if you have a fever of 102°F (39°C) or higher, blood or black stool, severe pain, or signs of '
             'dehydration (very dark urine, dizziness, a dry mouth, passing little urine). Young children, older adults and pregnant people should get advice sooner.'),
        Band('type-6', 6, 'Type 6: mushy stool',
             'Mushy stool with ragged edges is on the loose side. It is often short-lived, for example after a stomach bug, a rich meal, too much '
             'coffee or alcohol, a stressful week or a new medicine or supplement (magnesium is a common one).\n\n'
             'If it keeps coming back, it can have other causes such as food intolerances or irritable bowel syndrome, which only a doctor can tell apart.',
             ('Keep a short food and symptom diary for two weeks; patterns often show up that way.',
              'Cut back on common triggers for a while: lots of coffee, alcohol, sugar-free sweeteners (sorbitol, xylitol) and very fatty meals.',
              'Soluble fiber such as oats, bananas and cooked apples can help firm things up. Drink enough water.'),
             'See a doctor if loose stools last more than two weeks, wake you at night, come with weight loss, or if you see blood. '
             'If you are also very thirsty, dizzy or passing little urine, get care sooner.'),
        Band('type-5', 5, 'Type 5: soft blobs',
             'Soft pieces with clear edges that pass easily are on the softer side of normal. For many people this is simply how they are, '
             'especially if it happens after meals and they feel fine.\n\n'
             'It can also be a sign of food moving through a little faster than ideal, or of a diet low in fiber. If your stool is usually firmer and this is a change, '
             'think about what changed recently.',
             ('Add fiber you tolerate (oats, beans, vegetables, seeds) a little at a time and see whether stools become better formed.',
              'Eat regular meals and chew well; irregular eating can speed things up.',
              'If it is new and lasts, jot down what you eat and how you feel and see how it evolves over a couple of weeks.'),
             _DOCTOR_ANY, True),
        Band('type-4', 4, 'Type 4: smooth, soft sausage',
             'This is the shape doctors usually describe as the easiest to pass and the most typical of a gut that is working well. '
             'Normal is a range, though: anywhere from three times a day to three times a week can be healthy if it is steady and comfortable for you.\n\n'
             'Nothing here needs fixing. What helps keep it this way is the ordinary basics.',
             ('Keep up fluids, a variety of plant foods and daily movement.',
              'Try to go when you feel the urge instead of holding on.',
              'If you are curious about gentle abdominal self-massage as part of a calm routine, it is a pleasant extra, not a requirement.'),
             _DOCTOR_ANY, True),
        Band('type-3', 3, 'Type 3: sausage with cracks',
             'A sausage shape with cracks on the surface is within the normal range, slightly on the firm side. It often means there is room for a little more fluid or fiber, '
             'especially if passing it takes some effort.',
             ('Drink water regularly through the day.',
              'Add fruit, vegetables, oats or beans gradually, and keep an eye on how you feel.',
              'Move every day; even a daily walk helps the gut keep its rhythm.'),
             _DOCTOR_ANY, True),
        Band('type-2', 2, 'Type 2: lumpy sausage',
             'A lumpy, sausage-shaped stool is firm and bunched together. It usually points to mild constipation: stool is moving a little more slowly than ideal '
             'and giving up extra water on the way.\n\n'
             'It is very common, and often responds to ordinary habits. If it is your regular pattern and you feel fine, it may simply be your normal.',
             ('Drink water through the day and have a warm drink in the morning.',
              'Increase fiber slowly over a few weeks (oats, pears, kiwi, prunes, beans, vegetables, chia or ground flaxseed). A sudden jump can cause gas and bloating.',
              'Sit on the toilet for a few relaxed minutes after breakfast, when the gut is naturally most active, and rest your feet on a small stool so your knees sit above your hips.',
              'Walk or move every day.'),
             'See a doctor if stools stay hard for more than three weeks, if you strain a lot or feel you never empty completely, or if this is a change for you. '
             + _DOCTOR_ANY, True),
        Band('type-1', 1, 'Type 1: separate hard lumps',
             'Hard, separate lumps like nuts mean stool has been in the colon for a long time and has lost a lot of its water. It is hard to pass and is a common pattern of constipation.\n\n'
             'Many things can slow the gut: too little fluid or fiber, a change of routine, travel, stress, little movement, and some medicines or supplements '
             '(iron, certain painkillers and antacids). Sometimes a medical condition is behind it, which is why a lasting pattern deserves a conversation with a doctor.',
             ('Drink water regularly and add a warm drink to your morning routine.',
              'Raise fiber gradually, and drink more as you do; adding it fast can make bloating worse.',
              'Do not strain and do not hold it in. A few relaxed minutes on the toilet after breakfast, feet on a small stool, can help.',
              'Move every day. Gentle activity helps the gut move.'),
             'See a doctor if hard stools last more than three weeks, if you strain a lot or have pain when passing them, or if this is a new change. '
             'Get care promptly for severe belly pain with vomiting and no stool or gas passing. ' + _DOCTOR_ANY,
             True),
    ),
    note=('This test is for education only. It does not diagnose, treat or screen for any condition and does not replace advice from a doctor. '
          'If something worries you, or you are unsure, please talk with a healthcare professional.'),
    guide_slug='gut-basics',
    paid_guide_slug='gut-massage-guide',
    course_slug='massage-course',
)

SELF_TESTS: dict[str, SelfTest] = {BRISTOL.slug: BRISTOL}


# ---------------------------------------------------------------- guides

@dataclass(frozen=True)
class GuideSection:
    title: str
    paragraphs: tuple[str, ...]
    images: tuple[str, ...] = ()     # static names under img/ (without .webp): photos shown with the section, free to use commercially (docs/Image-credits)
    extra: str = ''                  # a visual block the template draws for this section: 'types', 'foods' or 'redflags'


@dataclass(frozen=True)
class Guide:
    """A guide in the library. A paid guide (`product_slug` set) shows its first `preview_sections` to everybody and the rest to people who bought it."""
    slug: str
    title: str
    subtitle: str
    tagline: str                         # on the library card
    icon: str
    key_idea: tuple[str, str]            # (label, text): the callout under the title
    sections: tuple[GuideSection, ...]
    steps_title: str
    steps: tuple[tuple[str, str], ...]   # (title, text)
    focus_title: str
    focus_intro: str
    focus_questions: tuple[str, ...]
    conclusion: str
    product_slug: str | None = None
    preview_sections: int = 0            # a paid guide: how many sections (from the start) are free to read
    highlights: tuple[str, ...] = ()     # a paid guide: what is inside, shown on the locked page

    @property
    def paid(self) -> bool:
        return self.product_slug is not None


GUT_BASICS = Guide(
    slug='gut-basics',
    title='Gut Basics: A Gentle Starter Guide',
    subtitle='Everyday habits that help a sluggish or unsettled gut, and how to tell when it is time to ask a professional',
    tagline='Free. Fluids, fiber, rhythm and movement, plus how to read your stool and when to see a doctor.',
    icon='leaf',
    key_idea=('The short version',
              'Your gut likes rhythm. Regular meals, enough fluid, fiber that you add slowly, daily movement and unhurried time on the toilet '
              'do more for most people than any single trick. And if something looks like a warning sign, getting it checked comes first.'),
    sections=(
        GuideSection('What "regular" actually means', (
            'There is no single normal. Doctors usually consider anything from three bowel movements a day to three a week healthy, as long as it is steady for you and comfortable.',
            'What matters more than the number is the pattern and how you feel. The Bristol Stool Scale describes stool in seven shapes: types 3 and 4 are the easiest to pass, '
            'types 1 and 2 point toward constipation, and types 6 and 7 toward loose stools. You can check where you are with the Bristol stool test on this site.',
        ), extra='types'),
        GuideSection('Water and fiber, in that order', (
            'Fiber adds bulk and holds water in the stool, but it needs fluid to work. If you raise fiber without drinking more, constipation can get worse.',
            'Add it slowly, over two to three weeks: oats, pears, kiwi, prunes, beans, lentils, vegetables, chia or ground flaxseed. A good start is one new fiber-rich food at a time. '
            'Drink water regularly through the day rather than all at once.',
        ), images=('gb-water', 'gb-fiber'), extra='foods'),
        GuideSection('Give your gut a rhythm', (
            'The gut is most active in the morning and after meals. Eating at regular times and keeping a few unhurried minutes after breakfast for the toilet works with that rhythm.',
            'Do not ignore the urge or strain against it. A small footstool that lifts your knees above your hips straightens the angle of the rectum and makes emptying easier for many people.',
        )),
        GuideSection('Movement and stress', (
            'Walking, stretching and anything that gets you moving help the gut move too. Even 20 minutes a day makes a difference for many people.',
            'The gut and the nervous system talk constantly, so stress can speed things up or slow them down. A few slow breaths before a meal or on the toilet is a small, free tool.',
        ), images=('gb-walk',)),
        GuideSection('When to talk to a doctor', (
            'Home habits are for ordinary, mild trouble. Please see a doctor if you notice blood in or on the stool, black or tar-like stool, unexplained weight loss, '
            'severe or lasting belly pain, diarrhea that wakes you at night, or a clear change from your usual pattern that lasts more than a few weeks.',
        ), extra='redflags'),
    ),
    steps_title='A gentle 7-day reset',
    steps=(
        ('Day 1: notice', 'Do not change anything yet. Write down when you eat, drink, move and go to the toilet, and the shape of the stool (the Bristol test helps).'),
        ('Day 2: water', 'Spread your fluids through the day, and start with a glass of water or a warm drink in the morning.'),
        ('Day 3: one fiber food', 'Add one fiber-rich food you enjoy, such as oats, a pear or a handful of beans.'),
        ('Day 4: the toilet routine', 'Spend a few unhurried minutes on the toilet after breakfast, feet on a small stool, no phone, no pushing.'),
        ('Day 5: move', 'Take a 20-minute walk, ideally after a meal.'),
        ('Day 6: slow down', 'Eat one meal without screens and chew well. Take three slow breaths before you start.'),
        ('Day 7: review', 'Look at your notes. What helped? Keep that and drop the rest. If nothing has changed after a few weeks, or a warning sign appears, book a visit with a doctor.'),
    ),
    focus_title='Questions to notice',
    focus_intro='A few quiet questions to ask yourself during the week.',
    focus_questions=(
        'When in the day does my gut feel most settled?',
        'What did I eat or drink the day before an easier or harder day?',
        'Do I hurry or hold on when I feel the urge?',
        'Is this a lasting pattern, or a change from what is normal for me?',
    ),
    conclusion='Small, steady habits work better than dramatic ones, and a warning sign is a reason to ask a professional, not to try one more trick at home.',
)

GUT_MASSAGE_GUIDE = Guide(
    slug='gut-massage-guide',
    title='Gut Massage Guide',
    subtitle='A gentle, practical way to look after your digestion, which many people find helps with bloating, with clear safety guidance',
    tagline='Paid. A calm, step-by-step approach to abdominal self-massage, with the safety guidance that comes with it.',
    icon='book-open-text',
    key_idea=('What it is for',
              'Abdominal self-massage is a gentle, comfortable routine that some people find helps with bloating and sluggish digestion. '
              'It is not a treatment for any condition, and it is not for everyone. The guide begins with who should skip it or ask a doctor first.'),
    product_slug='gut-massage-guide',
    preview_sections=1,
    highlights=(
        'Who should not do abdominal massage, and when to ask a doctor first',
        'How to set up: timing, position, pressure and warmth',
        'A step-by-step routine that follows the direction of the colon',
        'How often to practice and how to tell it is not right for you',
        'Everyday habits that support the routine',
    ),
    # TODO(owner): replace the sections below with the text of the finished guide.
    sections=(
        GuideSection('Before you begin: is it right for you?', (
            f'Skip abdominal massage and talk with a doctor first if any of these applies to you: {safety.sentence(safety.SKIP_CONDITIONS)}. '
            f'Ask your doctor before you start if you have {safety.sentence(safety.ASK_FIRST)}.',
            'If you are unsure, ask your doctor. Stop right away if anything hurts, and never press hard.',
        )),
        GuideSection('Setting up', (
            'Choose a time at least one to two hours after a large meal, and empty your bladder first. Lie on your back with your knees bent and a pillow under your head, in a warm, quiet place.',
            'Rest your hands on your belly and take a few slow breaths so the abdomen can soften.',
        )),
        GuideSection('The basic routine', (
            'Using the flat of your hand or fingertips with light to moderate pressure, move in slow circles clockwise around the navel, following the path of the colon: '
            'up on your right side, across under the ribs, and down on your left side.',
            'Keep the movement slow and relaxed for about five minutes. The pressure should always feel comfortable.',
        )),
        GuideSection('How often', (
            'Once or twice a day is plenty, for example in the morning or in the evening. Give it a few weeks before judging it, and keep notes on how you feel.',
            'If it makes you more uncomfortable, stop. If you are not getting better or symptoms are worrying you, see a doctor.',
        )),
    ),
    steps_title='A five-minute routine',
    steps=(
        ('Settle', 'Lie down, knees bent. Breathe slowly for one minute with your hands resting on your belly.'),
        ('Warm up', 'Make small, light circles around the navel, clockwise.'),
        ('Follow the colon', 'Widen the circles along the path of the colon, light pressure, slow pace.'),
        ('Finish', 'Rest your hands on your belly for a minute and breathe. Drink a glass of water.'),
    ),
    focus_title='Notice and adjust',
    focus_intro='After each session, a few things to notice.',
    focus_questions=(
        'Did my belly feel softer or more tense?',
        'Was there any pain or discomfort?',
        'What else changed that day: food, water, sleep, stress?',
    ),
    conclusion='Be gentle, be consistent, and let comfort set the pressure. Pair the routine with water, fiber, movement and rest, and see a doctor for anything that worries you.',
)

GUIDES: dict[str, Guide] = {g.slug: g for g in (GUT_BASICS, GUT_MASSAGE_GUIDE)}
