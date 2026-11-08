/**
 * Submission Desk: web dashboard stub.
 *
 * The Submission Desk page lives in Hermes Desktop (desktop/plugin.js). The web dashboard loads a
 * bundle for every plugin that has a dashboard/manifest.json, and this plugin has one only to mount
 * its Desktop backend routes. This bundle registers a short pointer to Desktop so the dashboard does
 * not request a missing file. Plain IIFE, no build step, no network calls.
 */
;(function () {
  'use strict'
  var registry = window.__HERMES_PLUGINS__
  var SDK = window.__HERMES_PLUGIN_SDK__
  if (!registry || typeof registry.register !== 'function' || !SDK || !SDK.React) return
  var h = SDK.React.createElement

  function SubmissionDeskPointer() {
    return h('div', { style: { padding: 24, maxWidth: 640, lineHeight: 1.5 } },
      h('h2', { style: { margin: '0 0 8px', fontSize: 18 } }, 'Submission Desk'),
      h('p', { style: { margin: 0 } },
        'Submission Desk runs in Hermes Desktop: open it from the Desktop sidebar. ' +
        'In chat, use /submission-desk; in a terminal, hermes submission-desk status.'))
  }

  registry.register('submission-desk', SubmissionDeskPointer)
})()
