/* The two drawn replays of failure patterns. Each step patches the state before it (see stage.js):
     at      where the pointer goes: a field ('first') or a button ('add'); click: true clicks
     fields  per-field changes: v (value), focus, popup, show, mark ('ok' | 'bad' | 'missing' | 'new')
     plan    the steps of the harness ({ id, text, st, note }; the note shows once a step is done),
             edited with CUA.plan helpers. A variant with a plan gets the console beside the screen,
             one without (the study's agents) a plain list of what the agent did
     boxes   evidence boxes on fields; toast: a message of the application
     say     what happened; who: plan | dec | code | sys | per | agent
   Requests are the study's task prompts (tasks.json), patients and values its synthetic data, field
   labels those of HospitalRun. The harness side follows the decision-model harness (branch
   decision_model_harness): steps and notes are worded as in its log. The recorded runs at the top of
   the page are not scenes; they are in runs.js and replay.js. */
(function () {
  'use strict';
  const { set, before } = window.CUA.plan;

  // ------------------------------- the phone number lands in the date field
  const focus = {
    base: {
      fields: { first: { v: 'John' }, last: { v: 'Doe' }, dob: { v: '' }, phone: { v: '' }, address: { v: '' } },
    },
    variants: {
      single: {
        base: { cap: 'A single-loop agent creates the patient record from the task above.' },
        steps: [
          {
            at: 'dob', click: true,
            fields: { dob: { v: '10/10/2000', focus: true, popup: true } },
            say: { who: 'agent', text: "`type` '10/10/2000' into Date Of Birth" },
            cap: 'The agent types the date of birth. A date picker opens below the field.',
          },
          {
            at: 'phone', click: true,
            fields: { dob: { popup: false } },
            say: { who: 'agent', text: '`click` Phone' },
            cap: 'It clicks the Phone field. The click closes the picker, but the focus stays in the date field.',
          },
          {
            fields: { dob: { v: '10/10/20000123 45678910', mark: 'bad' } },
            say: { who: 'agent', text: "`type` '0123 45678910'" },
            cap: 'It types the phone number, which lands in the date field.',
          },
          {
            at: 'dob', click: true, retype: ['dob'],
            fields: { dob: { v: '10/10/2000', mark: null } },
            say: { who: 'agent', text: "notices the digits in Date Of Birth · `type` '10/10/2000'" },
            cap: 'On a later screenshot the agent notices the digits and retypes the date. In the study, all three agents spotted this every time.',
          },
          {
            at: 'phone', click: true,
            fields: { dob: { focus: false }, phone: { v: '0123 45678910', focus: true } },
            say: { who: 'agent', text: "`click` Phone · `type` '0123 45678910'" },
            cap: 'It enters the number again, now in the Phone field. The agents fixed the slip in most runs, but not all: noticing and repairing it was left to the model alone.',
          },
        ],
      },
      harness: {
        base: {
          plan: [
            { id: 1, text: "Fill 'First Name *' with 'John'", st: 'done', note: "'John' found next to 'First Name *'" },
            { id: 2, text: "Fill 'Last Name *' with 'Doe'", st: 'done', note: "'Doe' found next to 'Last Name *'" },
            { id: 3, text: "Fill 'Date Of Birth' with '10/10/2000'", st: 'active', note: "'10/10/2000' found next to 'Date Of Birth'" },
            { id: 4, text: "Fill 'Phone' with '0123 45678910'", st: 'pending', note: "'0123 45678910' found next to 'Phone'" },
          ],
          cap: 'The harness on the same patient record. The planner has written the steps for this form; code carries them out.',
        },
        steps: [
          {
            at: 'dob', click: true,
            fields: { dob: { v: '10/10/2000', focus: true, popup: true } },
            say: { who: 'code', text: "`click` the field of 'Date Of Birth' · `type` '10/10/2000'" },
            cap: 'Code finds the field from its label, clicks it and types the date. The date picker opens.',
          },
          {
            say: { who: 'per', text: 'after typing, the screen changed outside the field: 37 new texts next to it' },
            cap: 'The screen is compared with the one before the action. New texts appeared next to the field, not in it: the field has opened a helper.',
          },
          {
            fields: { dob: { popup: false } },
            say: { who: 'code', text: 'the field opened a helper · `press` Esc' },
            cap: 'The helper is closed with Esc before anything else is typed, so nothing is left open over the next field.',
          },
          {
            fields: { dob: { focus: false } },
            boxes: [{ f: 'dob', tag: "'10/10/2000' next to 'Date Of Birth'" }],
            plan: set({ 3: 'done', 4: 'active' }),
            say: { who: 'sys', text: "'10/10/2000' found next to 'Date Of Birth' ✓" },
            cap: 'The step counts once its value is read next to its label.',
          },
          {
            at: 'phone', click: true,
            fields: { phone: { v: '0123 45678910', focus: true } },
            say: { who: 'code', text: "`click` the field of 'Phone' · `type` '0123 45678910'" },
            cap: 'With the picker gone, the click moves the focus and the number lands in the Phone field.',
          },
          {
            fields: { phone: { focus: false } },
            boxes: [{ f: 'phone', tag: "'0123 45678910' next to 'Phone'" }],
            plan: set({ 4: 'done' }),
            say: { who: 'sys', text: "'0123 45678910' found next to 'Phone' ✓" },
            cap: 'The same check again. Had the number still appeared next to another label, code would have deleted it there and reported the step as failed. None of this is left to a model.',
          },
        ],
      },
    },
  };

  // ---------------------------- the Radiologist field appears after X-Ray
  const dynamic = {
    base: {
      fields: { visit: { v: '' }, type: { v: '' }, rad: { v: '', show: false }, result: { v: '', show: false }, notes: { v: '' } },
    },
    variants: {
      single: {
        base: { cap: 'A single-loop agent files the imaging request from the task above.' },
        steps: [
          {
            at: 'visit', click: true,
            fields: { visit: { v: '08/05/2025' } },
            say: { who: 'agent', text: "`click` Visit · select '08/05/2025'" },
            cap: 'The agent selects the visit.',
          },
          {
            at: 'type', click: true,
            fields: { type: { v: 'X-Ray', focus: true } },
            say: { who: 'agent', text: "`type` 'X-Ray' into Imaging Type" },
            cap: 'It types the imaging type.',
          },
          {
            fields: { rad: { show: true, mark: 'new' }, result: { show: true, mark: 'new' } },
            cap: 'The form reacts to the entry: two new fields, Radiologist and Result, appear.',
          },
          {
            at: 'add', click: true, press: 'add', toast: 'Imaging request saved',
            fields: { type: { focus: false }, rad: { mark: 'missing' }, result: { mark: null } },
            say: { who: 'agent', text: '`click` Add' },
            cap: 'The agent goes on to Add as if the form had not changed, and the request is saved without the radiologist. In the study, two of the three agents sometimes did this.',
          },
        ],
      },
      harness: {
        base: {
          fields: { visit: { v: '08/05/2025' } },
          plan: [
            { id: 1, text: "Select '08/05/2025' in 'Visit *'", st: 'done', note: "'08/05/2025' found next to 'Visit *'" },
            { id: 2, text: "Fill 'Imaging Type *' with 'X-Ray'", st: 'active', note: "'X-Ray' found next to 'Imaging Type *'" },
            { id: 3, text: "Click 'Add'", st: 'pending', note: "New on screen: 'Imaging request saved'" },
          ],
          cap: 'The harness on the same imaging request. The planner wrote the steps from the form as it looked at first: visit, imaging type, Add.',
        },
        steps: [
          {
            at: 'type', click: true,
            fields: { type: { v: 'X-Ray', focus: true } },
            say: { who: 'code', text: "`click` the field of 'Imaging Type *' · `type` 'X-Ray'" },
            cap: 'Code types the imaging type.',
          },
          {
            fields: { rad: { show: true, mark: 'new' }, result: { show: true, mark: 'new' } },
            say: { who: 'per', text: "new on screen: 'Radiologist', 'Result' and two fields without text" },
            cap: 'The form adds two fields. Comparing the screen before and after the action lists them as new.',
          },
          {
            fields: { type: { focus: false } },
            boxes: [{ f: 'type', tag: "'X-Ray' next to 'Imaging Type *'" }],
            plan: set({ 2: 'done' }),
            say: { who: 'sys', text: "'X-Ray' found next to 'Imaging Type *' ✓" },
            cap: 'The entry itself is checked as usual.',
          },
          {
            say: { who: 'dec', text: 'Do the planned steps still fit what appeared? `replan`' },
            cap: 'Something appeared that the plan did not foresee. The decider is asked one short question: can the remaining steps go on as planned? Its answer is that the plan has to be looked at again.',
          },
          {
            fields: { rad: { mark: null }, result: { mark: null } },
            plan: before(3, { id: 4, text: "Fill 'Radiologist' with 'Marie Curie'", st: 'active', note: "'Marie Curie' found next to 'Radiologist'" }),
            say: { who: 'plan', text: "`steps` Fill 'Radiologist' with 'Marie Curie', before Add", thought: 'A Radiologist field appeared; the request names Marie Curie.' },
            cap: 'Only now is the planner called. It reads the screen and returns what has to happen before the steps still planned.',
          },
          {
            at: 'rad', click: true,
            fields: { rad: { v: 'Marie Curie', focus: true } },
            say: { who: 'code', text: "`click` the field of 'Radiologist' · `type` 'Marie Curie'" },
            cap: 'Code fills in the new field.',
          },
          {
            fields: { rad: { focus: false } },
            boxes: [{ f: 'rad', tag: "'Marie Curie' next to 'Radiologist'" }],
            plan: set({ 4: 'done', 3: 'active' }),
            say: { who: 'sys', text: "'Marie Curie' found next to 'Radiologist' ✓" },
            cap: 'Checked against its label, so Add is next.',
          },
          {
            at: 'add', click: true, press: 'add', toast: 'Imaging request saved',
            plan: set({ 3: 'done' }),
            say: { who: 'code', text: "`click` 'Add'" },
            cap: 'Add saves the imaging request, with the radiologist.',
          },
          {
            toast: '',
            say: { who: 'dec', text: 'Is the request complete? `yes`' },
            cap: 'The run ends when the decider confirms that the request is complete, judging by the steps carried out and what the screen shows.',
          },
        ],
      },
    },
  };

  window.CUA_SCENES = { focus, dynamic };
})();
