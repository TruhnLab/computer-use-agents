/* The scenes. Each step patches the state before it (see stage.js):
     at      where the pointer goes: a field ('first'), a button ('add') or a menu option ('visit@2'); click: true clicks
     fields  per-field changes: v (value), focus, popup, show, mark ('ok' | 'bad' | 'missing' | 'new')
     plan    the supervisor's to-do list, edited with CUA.plan helpers; planDelay holds it back (ms)
     fly     a chip flying between a plan item and the form: from, to (selectors), text, kind ('report')
     glow    the pane that is working ('sup' | 'act'); count: the verified counter in the window bar
     snap    a screenshot, with the boxes perception finds on it; scan: sweep the check over a field
     view    the application screen shown (teaser examples with several screens); boxes: evidence boxes
     say     messages added to the log; who: sup | act | sys | per | alarm | agent
   Element IDs, tool names and message wording follow the harness (agent.py on the
   multi_agent_harness branch). Requests are the study's task prompts (tasks.json), patients and
   values its synthetic data, field labels those of HospitalRun. */
(function () {
  'use strict';
  const { add, set, front, seq } = window.CUA.plan;

  // ------------------------------- 1 (teaser): four study tasks, each from request to verified record
  // Snappy on purpose: every step has a fixed duration, and the captions only reach screen readers.
  const item = id => `[data-plan] [data-id="${id}"]`;
  const into = (id, f, text) => ({ from: item(id), to: `[data-f="${f}"] .inp`, text });
  const claim = (f, id, text) => ({ from: `[data-f="${f}"] .inp`, to: item(id), text, kind: 'report' });
  const flag = (id, rej) => p => p.map(it => (it.id === id ? Object.assign({}, it, { rej }) : it));
  const verify = (phase, f, n, total, planEdit) => ({
    phase, scan: f, planDelay: 420, dur: 680,
    fields: { [f]: { mark: 'ok', focus: false } },
    count: `${n} / ${total} verified`,
    plan: planEdit,
  });
  const buttons = save => [{ b: 'cancel', text: 'Cancel', ghost: true }, { b: 'add', text: save || 'Add' }];
  const patient = { f: 'patient', label: 'Patient', kind: 'static wide-f', value: 'John Doe · P00001' };
  const saved = (phase, what, last) => ({
    phase, dur: 2800, at: 'add', click: true, press: 'add', toast: `${what} · every entry verified`, plan: set({ [last]: 'done' }),
  });

  const newPatient = {
    phases: ['Request', 'Plan', 'Act & verify', 'Catch a slip', 'Finish'],
    views: {
      main: {
        title: 'New Patient',
        fields: [
          { f: 'first', label: 'First Name *' }, { f: 'last', label: 'Last Name *' },
          { f: 'sex', label: 'Sex', kind: 'sel' }, { f: 'dob', label: 'Date Of Birth', kind: 'date' },
          { f: 'phone', label: 'Phone' }, { f: 'address', label: 'Address' },
        ],
        buttons: buttons(),
      },
    },
    base: {
      fields: { first: { v: '' }, last: { v: '' }, sex: { v: '' }, dob: { v: '' }, phone: { v: '' }, address: { v: '' } },
      cap: 'Example: a clinician asks for a new patient record.',
    },
    steps: [
      { phase: 0, dur: 2700, request: 'Create a new Patient profile with the following specifications: First Name: John, Last Name: Doe, Sex: Male, Date of Birth: 10/10/2000, Phone: 0123 45678910, Address: Pauwelsstraße 30, 52070 Aachen.' },
      {
        phase: 1, glow: 'sup', dur: 1000,
        plan: add({ id: 1, text: 'First Name: John' }, { id: 2, text: 'Last Name: Doe' }, { id: 3, text: 'Sex: Male' }),
        cap: 'The supervisor plans only the next few steps, from text. It never sees the screen.',
      },
      {
        phase: 2, glow: 'act', snap: true, dur: 1200,
        plan: set({ 1: 'active' }), fly: into(1, 'first', 'John'),
        at: 'first', click: true, fields: { first: { v: 'John', focus: true } },
        cap: 'The actioner takes a screenshot, then works the real screen with mouse and keyboard. Every entry is checked before it counts.',
      },
      verify(2, 'first', 1, 6, seq(set({ 1: 'done' }), add({ id: 4, text: 'Date of Birth: 10/10/2000' }))),
      { phase: 2, glow: 'act', snap: true, dur: 1100, plan: set({ 2: 'active' }), fly: into(2, 'last', 'Doe'), at: 'last', click: true, fields: { last: { v: 'Doe', focus: true } } },
      verify(2, 'last', 2, 6, seq(set({ 2: 'done' }), add({ id: 5, text: 'Phone: 0123 45678910' }))),
      { phase: 2, glow: 'act', snap: true, dur: 1050, plan: set({ 3: 'active' }), fly: into(3, 'sex', 'Male'), at: 'sex', click: true, fields: { sex: { v: 'Male' } } },
      verify(2, 'sex', 3, 6, seq(set({ 3: 'done' }), add({ id: 6, text: 'Address: Pauwelsstraße 30, 52070 Aachen' }))),
      { phase: 2, glow: 'act', snap: true, dur: 1150, plan: set({ 4: 'active' }), fly: into(4, 'dob', '10/10/2000'), at: 'dob', click: true, fields: { dob: { v: '10/10/2000', focus: true, popup: true } } },
      Object.assign(verify(2, 'dob', 4, 6, set({ 4: 'done' })), { fields: { dob: { mark: 'ok' } } }),
      {
        phase: 3, glow: 'act', snap: true, dur: 1350,
        plan: set({ 5: 'active' }), fly: into(5, 'phone', '0123 45678910'),
        at: 'phone', click: true, fields: { dob: { popup: false, mark: null, v: '10/10/20000123 45678910' } },
        cap: 'A value lands in the wrong field. The check catches it at once, and the supervisor puts a fix first.',
      },
      {
        phase: 3, alarm: true, dur: 1300, count: '3 / 6 verified',
        fields: { dob: { mark: 'bad' } },
        boxes: [{ f: 'dob', kind: 'bad', tag: 'wrong field' }, { f: 'phone', kind: 'want', tag: 'expected here' }],
      },
      { phase: 3, glow: 'sup', dur: 900, plan: seq(set({ 5: 'pending' }), front({ id: 7, text: 'Fix Date of Birth', st: 'active' })) },
      { phase: 3, glow: 'act', snap: true, dur: 1000, at: 'dob', click: true, fields: { dob: { v: '10/10/2000', focus: true } } },
      { phase: 3, scan: 'dob', planDelay: 420, dur: 680, count: '4 / 6 verified', fields: { dob: { mark: 'ok', focus: false } }, plan: set({ 7: 'done', 5: 'active' }) },
      {
        phase: 4, glow: 'act', snap: true, dur: 1150,
        fly: into(5, 'phone', '0123 45678910'), at: 'phone', click: true, fields: { phone: { v: '0123 45678910', focus: true } },
        cap: 'The record is completed and saved, with every entry verified on screen.',
      },
      verify(4, 'phone', 5, 6, set({ 5: 'done', 6: 'active' })),
      { phase: 4, glow: 'act', snap: true, dur: 1600, fly: into(6, 'address', 'Pauwelsstraße 30, 52070 Aachen'), at: 'address', click: true, fields: { address: { v: 'Pauwelsstraße 30, 52070 Aachen', focus: true } } },
      verify(4, 'address', 6, 6, set({ 6: 'done' })),
      { phase: 4, dur: 2800, at: 'add', click: true, press: 'add', toast: 'Patient saved · every entry verified' },
    ],
  };

  const imaging = {
    phases: ['Request', 'Plan', 'Act & verify', 'Form changes', 'Finish'],
    views: {
      main: {
        title: 'New Imaging Request',
        fields: [patient, { f: 'visit', label: 'Visit *', kind: 'sel' }, { f: 'type', label: 'Imaging Type *' },
          { f: 'rad', label: 'Radiologist' }, { f: 'result', label: 'Result' }, { f: 'notes', label: 'Notes', kind: 'wide-f tall' }],
        buttons: buttons(),
      },
    },
    base: {
      fields: { visit: { v: '' }, type: { v: '' }, rad: { v: '', show: false }, result: { v: '', show: false }, notes: { v: '' } },
      cap: 'Example: a clinician asks for an X-ray request.',
    },
    steps: [
      { phase: 0, dur: 2200, request: 'Please add a new X-Ray imaging request for patient P00001 and the visit on 08/05/2025. The associated radiologist is Marie Curie.' },
      { phase: 1, glow: 'sup', dur: 950, plan: add({ id: 1, text: 'Visit: 08/05/2025' }, { id: 2, text: 'Imaging Type: X-Ray' }), cap: 'The supervisor plans the steps the current screen allows.' },
      { phase: 2, glow: 'act', snap: true, dur: 1150, plan: set({ 1: 'active' }), fly: into(1, 'visit', '08/05/2025'), at: 'visit', click: true, fields: { visit: { v: '08/05/2025' } } },
      verify(2, 'visit', 1, 3, set({ 1: 'done' })),
      { phase: 2, glow: 'act', snap: true, dur: 1100, plan: set({ 2: 'active' }), fly: into(2, 'type', 'X-Ray'), at: 'type', click: true, fields: { type: { v: 'X-Ray', focus: true } } },
      verify(2, 'type', 2, 3, set({ 2: 'done' })),
      {
        phase: 3, snap: true, dur: 1400,
        fields: { rad: { show: true, mark: 'new' }, result: { show: true, mark: 'new' } },
        cap: 'The form changes after the entry: a Radiologist field appears. The plan grows from what is now on screen.',
      },
      {
        phase: 3, glow: 'sup', dur: 1000,
        fields: { rad: { mark: null }, result: { mark: null } },
        plan: add({ id: 3, text: 'Radiologist: Marie Curie', st: 'active' }, { id: 4, text: 'Save the request' }),
      },
      { phase: 3, glow: 'act', snap: true, dur: 1200, fly: into(3, 'rad', 'Marie Curie'), at: 'rad', click: true, fields: { rad: { v: 'Marie Curie', focus: true } } },
      verify(3, 'rad', 3, 3, set({ 3: 'done', 4: 'active' })),
      saved(4, 'Imaging request saved', 4),
    ],
  };

  const medication = {
    phases: ['Request', 'Plan', 'Claim rejected', 'Act & verify', 'Finish'],
    views: {
      main: {
        title: 'New Medication Request',
        fields: [patient, { f: 'visit', label: 'Visit *', kind: 'sel', options: ['08/05/2025', '08/31/2025', '09/01/2025', '09/10/2025'] },
          { f: 'med', label: 'Medication' }, { f: 'presc', label: 'Prescription' },
          { f: 'qty', label: 'Quantity Requested' }, { f: 'refills', label: 'Refills' }],
        buttons: buttons(),
      },
    },
    base: {
      fields: { visit: { v: '' }, med: { v: '' }, presc: { v: '' }, qty: { v: '' }, refills: { v: '' } },
      cap: 'Example: a clinician asks for a medication request.',
    },
    steps: [
      { phase: 0, dur: 2200, request: 'Please request a new medication for patient P00001 and the visit on 09/01/2025: Omeprazol 10 mg, Prescription: 1-0-0, Quantity: 5.' },
      { phase: 1, glow: 'sup', dur: 1000, plan: add({ id: 1, text: 'Visit: 09/01/2025' }, { id: 2, text: 'Medication: Omeprazol 10 mg' }, { id: 3, text: 'Prescription: 1-0-0' }) },
      { phase: 2, glow: 'act', snap: true, dur: 950, plan: set({ 1: 'active' }), at: 'visit', click: true, fields: { visit: { popup: true } } },
      {
        phase: 2, glow: 'act', dur: 950, at: 'visit@3', click: true, fields: { visit: { v: '09/10/2025', popup: false } },
        cap: 'In a tight dropdown, the actioner picks the neighbouring date and claims the item is done.',
      },
      { phase: 2, glow: 'act', dur: 1000, fly: claim('visit', 1, "done · '09/01/2025'") },
      {
        phase: 2, scan: 'visit', dur: 1500,
        boxes: [{ f: 'visit', kind: 'bad', tag: "'09/01/2025' not found" }], plan: flag(1, true),
        cap: 'The check finds a different date next to the Visit label, so the claim is rejected and the item stays open.',
      },
      { phase: 2, glow: 'act', snap: true, dur: 900, plan: flag(1, false), at: 'visit', click: true, fields: { visit: { popup: true } } },
      { phase: 2, glow: 'act', dur: 950, at: 'visit@2', click: true, fields: { visit: { v: '09/01/2025', popup: false } } },
      verify(2, 'visit', 1, 4, seq(set({ 1: 'done' }), add({ id: 4, text: 'Quantity Requested: 5' }))),
      {
        phase: 3, glow: 'act', snap: true, dur: 1300, plan: set({ 2: 'active' }), fly: into(2, 'med', 'Omeprazol 10 mg'),
        at: 'med', click: true, fields: { med: { v: 'Omeprazol 10 mg', focus: true } },
        cap: 'Only verified entries count. The rest of the request follows the same loop.',
      },
      verify(3, 'med', 2, 4, set({ 2: 'done' })),
      { phase: 3, glow: 'act', snap: true, dur: 1050, plan: set({ 3: 'active' }), fly: into(3, 'presc', '1-0-0'), at: 'presc', click: true, fields: { presc: { v: '1-0-0', focus: true } } },
      verify(3, 'presc', 3, 4, seq(set({ 3: 'done' }), add({ id: 5, text: 'Save the request' }))),
      { phase: 3, glow: 'act', snap: true, dur: 1000, plan: set({ 4: 'active' }), fly: into(4, 'qty', '5'), at: 'qty', click: true, fields: { qty: { v: '5', focus: true } } },
      verify(3, 'qty', 4, 4, set({ 4: 'done', 5: 'active' })),
      saved(4, 'Medication request saved', 5),
    ],
  };

  const checkin = {
    phases: ['Request', 'Plan', 'Find the patient', 'Act & verify', 'Finish'],
    views: {
      list: {
        title: 'Patients',
        fields: [{ f: 'search', label: 'Search patients', kind: 'wide-f' }, { f: 'hit', kind: 'row wide-f', value: 'John Doe · P00001 · Male' }],
        buttons: [{ b: 'checkin', text: 'Patient Check In' }],
      },
      form: {
        title: 'Patient Check In',
        fields: [patient, { f: 'vtype', label: 'Visit Type', kind: 'sel', options: ['Admission', 'Clinic', 'Followup', 'Imaging', 'Lab', 'Pharmacy'] },
          { f: 'when', label: 'Check In', kind: 'static', value: 'Today, 09:41' }, { f: 'examiner', label: 'Examiner' },
          { f: 'location', label: 'Location' }, { f: 'reason', label: 'Reason For Visit', kind: 'wide-f tall' }],
        buttons: buttons('Check In'),
      },
    },
    base: {
      view: 'list',
      fields: { search: { v: '' }, hit: { show: false }, vtype: { v: '' }, examiner: { v: '' }, location: { v: '' }, reason: { v: '' } },
      cap: 'Example: a clinician asks to check a patient in.',
    },
    steps: [
      { phase: 0, dur: 2200, request: 'Please locate and check in patient P00001 for a followup visit type now. The stated reason for the visit is persistent tiredness.' },
      { phase: 1, glow: 'sup', dur: 950, plan: add({ id: 1, text: 'Find patient P00001' }, { id: 2, text: 'Open Patient Check In' }) },
      {
        phase: 2, glow: 'act', snap: true, dur: 1150, plan: set({ 1: 'active' }), fly: into(1, 'search', 'P00001'),
        at: 'search', click: true, fields: { search: { v: 'P00001', focus: true } },
        cap: 'The actioner searches the patient list, the way a person would.',
      },
      { phase: 2, dur: 700, fields: { hit: { show: true } } },
      { phase: 2, scan: 'hit', planDelay: 420, dur: 700, fields: { hit: { mark: 'ok' }, search: { focus: false } }, plan: set({ 1: 'done', 2: 'active' }) },
      {
        phase: 2, glow: 'act', snap: true, dur: 1250, at: 'checkin', click: true, press: 'checkin', view: 'form',
        cap: 'A new screen opens. The supervisor plans the next steps from what it shows.',
      },
      {
        phase: 3, glow: 'sup', dur: 1100,
        plan: seq(set({ 2: 'done' }), add({ id: 3, text: 'Visit Type: Followup', st: 'active' }, { id: 4, text: 'Reason For Visit: persistent tiredness' }, { id: 5, text: 'Check the patient in' })),
      },
      { phase: 3, glow: 'act', snap: true, dur: 1050, fly: into(3, 'vtype', 'Followup'), at: 'vtype', click: true, fields: { vtype: { popup: true } } },
      { phase: 3, glow: 'act', dur: 900, at: 'vtype@2', click: true, fields: { vtype: { v: 'Followup', popup: false } } },
      verify(3, 'vtype', 1, 2, set({ 3: 'done', 4: 'active' })),
      { phase: 3, glow: 'act', snap: true, dur: 1350, fly: into(4, 'reason', 'persistent tiredness'), at: 'reason', click: true, fields: { reason: { v: 'persistent tiredness', focus: true } } },
      verify(3, 'reason', 2, 2, set({ 4: 'done', 5: 'active' })),
      saved(4, 'Patient checked in', 5),
    ],
  };

  const roles = {
    cycle: true,   // the examples play one after another
    speed: { type: 24, move: 380, fly: 620, scan: 420, request: 11 },
    phases: newPatient.phases,
    variants: { patient: newPatient, imaging, medication, checkin },
  };

  // ------------------------------- 4a: the phone number lands in the date field
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
            { id: 1, text: "Fill First Name with 'John'", st: 'done' },
            { id: 2, text: "Fill Last Name with 'Doe'", st: 'done' },
            { id: 3, text: "Fill Date Of Birth with '10/10/2000'", st: 'active' },
            { id: 4, text: "Fill Phone with '0123 45678910'" },
          ],
          cap: 'The harness on the same patient record. The supervisor’s plan is on the right.',
        },
        steps: [
          {
            at: 'dob', click: true,
            fields: { dob: { v: '10/10/2000', focus: true, popup: true } },
            say: { who: 'act', text: "`click_and_type` [e23] '10/10/2000'" },
            cap: 'The actioner types the date of birth, and the date picker opens.',
          },
          {
            boxes: [{ f: 'dob', tag: "'10/10/2000' next to 'Date Of Birth'" }],
            plan: set({ 3: 'done', 4: 'active' }),
            say: [{ who: 'sys', text: "'10/10/2000' found next to 'Date Of Birth' ✓" }, { who: 'sup', text: '`update_plan` item 3 done' }],
            cap: 'The evidence check passes and the supervisor moves on to the Phone item.',
          },
          {
            at: 'phone', click: true,
            fields: { dob: { popup: false, v: '10/10/20000123 45678910' } },
            say: { who: 'act', text: "`click_and_type` [e27] '0123 45678910'" },
            cap: 'The actioner clicks Phone and types in one action. As in the study, the focus never left the date field.',
          },
          {
            alarm: true,
            fields: { dob: { mark: 'bad' } },
            boxes: [{ f: 'dob', kind: 'bad', tag: 'found next to Date Of Birth' }, { f: 'phone', kind: 'want', tag: 'expected next to Phone' }],
            say: { who: 'alarm', text: "'0123 45678910' appeared next to 'Date Of Birth', but was expected next to 'Phone'." },
            cap: 'The wrong-field check runs after every action, so the slip is flagged at once, without relying on the model to notice it.',
          },
          {
            fields: { dob: { mark: 'bad' } },
            plan: seq(set({ 4: 'pending' }), front({ id: 5, text: "Restore Date Of Birth to '10/10/2000'", st: 'active' })),
            say: { who: 'sup', text: '`update_plan` Phone not done · add corrective item' },
            cap: 'The supervisor is called right away. Phone stays open, and a correction goes to the front of the queue: mistakes in the record are fixed before any new entry.',
          },
          {
            at: 'dob', click: true, retype: ['dob'],
            fields: { dob: { v: '10/10/2000', focus: true, mark: null } },
            say: { who: 'act', text: "`keypress` ctrl+a · `type_text` '10/10/2000'" },
            cap: 'The actioner selects the content of the date field and types the date again.',
          },
          {
            fields: { dob: { focus: false } },
            boxes: [{ f: 'dob', tag: "'10/10/2000' next to 'Date Of Birth'" }],
            plan: set({ 5: 'done', 4: 'active' }),
            say: { who: 'sys', text: "'10/10/2000' found next to 'Date Of Birth' ✓" },
            cap: 'The correction is itself verified before the Phone item becomes active again.',
          },
          {
            at: 'phone', click: true,
            fields: { phone: { v: '0123 45678910', focus: true } },
            say: { who: 'act', text: "`click_and_type` [e27] '0123 45678910'" },
            cap: 'With the picker closed, the click moves the focus and the number lands in the Phone field.',
          },
          {
            fields: { phone: { focus: false } },
            boxes: [{ f: 'phone', tag: "'0123 45678910' next to 'Phone'" }],
            plan: set({ 4: 'done' }),
            say: { who: 'sys', text: "'0123 45678910' found next to 'Phone' ✓" },
            cap: 'Caught one action after it happened, repaired as a planned and verified step, before anything else was entered.',
          },
        ],
      },
    },
  };

  // ---------------------------- 4b: the Radiologist field appears after X-Ray
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
            { id: 1, text: 'Open a new imaging request for P00001', st: 'done' },
            { id: 2, text: 'Select the visit on 08/05/2025', st: 'done' },
            { id: 3, text: "Fill Imaging Type with 'X-Ray'", st: 'active' },
          ],
          cap: 'The harness on the same imaging request. The visit is selected; the supervisor has planned up to the imaging type.',
        },
        steps: [
          {
            at: 'type', click: true,
            fields: { type: { v: 'X-Ray', focus: true } },
            say: { who: 'act', text: "`click_and_type` [e41] 'X-Ray'" },
            cap: 'The actioner types the imaging type.',
          },
          {
            fields: { rad: { show: true, mark: 'new' }, result: { show: true, mark: 'new' } },
            say: { who: 'per', text: "appeared: [e52] 'Radiologist', [e53] <interactive>, [e54] 'Result', [e55] <interactive>" },
            cap: 'The form adds two fields. Comparing the frame before and after the action records them as new elements.',
          },
          {
            fields: { type: { focus: false } },
            boxes: [{ f: 'type', tag: "'X-Ray' next to 'Imaging Type *'" }],
            plan: set({ 3: 'done' }),
            say: { who: 'sys', text: "'X-Ray' found next to 'Imaging Type *' ✓" },
            cap: 'The item is verified. That completes it, so the supervisor is called.',
          },
          {
            fields: { rad: { mark: null }, result: { mark: null } },
            plan: add({ id: 4, text: "Fill Radiologist with 'Marie Curie'", st: 'active' }, { id: 5, text: 'Press Add to save the request' }),
            say: { who: 'sup', text: '`update_plan` add 2 items', thought: 'assessment: a Radiologist field appeared; the request names Marie Curie.' },
            cap: 'The plan is extended after every item, from the current screen. The new Radiologist field is in view now, so the request’s Marie Curie gets her own item before saving.',
          },
          {
            at: 'rad', click: true,
            fields: { rad: { v: 'Marie Curie', focus: true } },
            say: { who: 'act', text: "`click_and_type` [e53] 'Marie Curie'" },
            cap: 'The actioner fills in the new field.',
          },
          {
            fields: { rad: { focus: false } },
            boxes: [{ f: 'rad', tag: "'Marie Curie' next to 'Radiologist'" }],
            plan: set({ 4: 'done', 5: 'active' }),
            say: { who: 'sys', text: "'Marie Curie' found next to 'Radiologist' ✓" },
            cap: 'Verified against its label, so the save item is next.',
          },
          {
            at: 'add', click: true, press: 'add', toast: 'Imaging request saved',
            plan: set({ 5: 'done' }),
            say: { who: 'act', text: '`click` [e60] Add' },
            cap: 'The actioner presses Add and the imaging request is saved.',
          },
          {
            toast: '',
            boxes: [{ f: 'type', tag: "'X-Ray' next to 'Imaging Type *'" }, { f: 'rad', tag: "'Marie Curie' next to 'Radiologist'" }],
            say: [
              { who: 'sup', text: "`update_plan` task_complete · final evidence: 'X-Ray', 'Marie Curie'" },
              { who: 'sys', text: 'final evidence: 2 of 2 found next to their labels ✓' },
            ],
            cap: 'Only the supervisor can finish the task, and only with values from the final screen that pass the same check.',
          },
        ],
      },
    },
  };

  window.CUA_SCENES = { roles, focus, dynamic };
})();
