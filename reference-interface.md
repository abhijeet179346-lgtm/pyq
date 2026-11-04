# PYQ Vault reference interface adaptation

## Source review

The user-supplied archive was extracted for review under `work/reference/PYQ-vault-main` in the parent workspace. It contains a React/TypeScript frontend, a TypeScript server, Tailwind styles, client context and bundled question data. Its files were inspected as reference source; its server was not executed.

## Implemented in Django

- Top navigation and compact account controls replace the core learner sidebar.
- Indigo gradient hero, coloured exam cards, study-tool cards and actual learner statistics.
- Live available-question counts and year ranges from published, accessible records.
- Exam → subject → chapter browsing, plus year/search filters and pagination.
- Question answer cards, multi-select guidance, source links and answer feedback.
- Local mathematical typesetting.
- Light/dark themes with a saved browser preference and mobile bottom navigation.
- Core login styling follows the indigo palette.
- Links and cards use the existing tenant feature settings. Django admin remains the control surface.

The existing Django authentication, email verification, tenancy and question data power these screens. The supplied React app is not required to run them.

## Reference modules requiring further porting

The reference also contains dedicated planner, audio flashcards, bookmarks, handwritten-note folders, formula books, support/donations, student profiles and mistake-reflection screens. Those modules have not been ported by this interface update. Existing Django materials, progress, institute tests, AI tutoring and content-import workflows remain their own implementations; this update does not claim screen-for-screen feature parity.

## Checks

Django system checks passed. Render checks returned HTTP 200 for the learner home, exam selection, the three exam subject pages, a question page, study library and progress. No test suite was run for this interface update.
