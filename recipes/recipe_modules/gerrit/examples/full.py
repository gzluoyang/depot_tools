# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'gerrit',
    'recipe_engine/step',
]


def RunSteps(api):
  host = 'https://chromium-review.googlesource.com'
  project = 'v8/v8'

  branch = 'test'
  commit = '67ebf73496383c6777035e374d2d664009e2aa5c'

  data = api.gerrit.create_gerrit_branch(host, project, branch, commit)
  assert data == 'refs/heads/test'

  data = api.gerrit.get_gerrit_branch(host, project, 'master')
  assert data == '67ebf73496383c6777035e374d2d664009e2aa5c'

  api.gerrit.move_changes(host, project, 'master', 'main')

  change = api.gerrit.create_change(host, project, 'main', 'Dummy CL.')
  assert change==str(91827), change_number

  api.gerrit.change_edit(host, change, 'chrome/version', 'new version')
  api.gerrit.publish_edit(host, change)
  api.gerrit.submit_change(host, change)

  # Query for changes in Chromium's CQ.
  api.gerrit.get_changes(
      host,
      query_params=[
        ('project', 'chromium/src'),
        ('status', 'open'),
        ('label', 'Commit-Queue>0'),
      ],
      start=1,
      limit=1,
  )
  related_changes = api.gerrit.get_related_changes(host,
                                                   change='58478',
                                                   revision='2')
  assert len(related_changes["changes"]) == 1

  # Query which returns no changes is still successful query.
  empty_list = api.gerrit.get_changes(
      host,
      query_params=[
        ('project', 'chromium/src'),
        ('status', 'open'),
        ('label', 'Commit-Queue>2'),
      ],
      name='changes empty query',
  )
  assert len(empty_list) == 0

  api.gerrit.get_change_description(
      host, change=123, patchset=1)

  api.gerrit.abandon_change(host, 123, 'bad roll')

  with api.step.defer_results():
    api.gerrit.get_change_description(
        host,
        change=122,
        patchset=3,
        step_test_data=api.gerrit.test_api.get_empty_changes_response_data)


def GenTests(api):
  yield (
      api.test('basic') +
      api.step_data('gerrit create_gerrit_branch (v8/v8 test)',
                    api.gerrit.make_gerrit_create_branch_response_data()) +
      api.step_data('gerrit create change at (v8/v8 main)', api.gerrit.create_change_response_data()) +
      api.step_data('gerrit get_gerrit_branch (v8/v8 master)',
                    api.gerrit.make_gerrit_get_branch_response_data()) +
      api.step_data('gerrit move changes',
                    api.gerrit.get_move_change_response_data(branch='main')) +
      api.step_data('gerrit relatedchanges',
                    api.gerrit.get_related_changes_response_data()) +
      api.step_data('gerrit changes empty query',
                    api.gerrit.get_empty_changes_response_data()))
