'use strict';

// Verify the exact host-Ollama path Knoxx uses for translation and OpenPlanner
// embeddings. verify.sh evaluates this file inside knoxx-backend, so the probe
// crosses the same dedicated host-bridge route and sees the same container environment
// as the application. PROBE_SELFTEST replaces fetch and never uses the network.

const REQUIRED_TRANSLATION_MODEL = 'gemma4:e2b';
const TRANSLATION_SCHEMA = Object.freeze({
  type: 'object',
  additionalProperties: false,
  properties: {
    translated_text: {type: 'string', minLength: 1},
  },
  required: ['translated_text'],
});
const TRANSLATION_SOURCE_TEXT = 'The deployment document is ready for human review.';
const INFERENCE_PROMPT = [
  'Translate the following document text from English to Spanish.',
  'Preserve its meaning and return only the requested structured JSON.',
  `JSON schema: ${JSON.stringify(TRANSLATION_SCHEMA)}`,
  `Source text: ${JSON.stringify(TRANSLATION_SOURCE_TEXT)}`,
].join(' ');
const TOOL_PROBE = Object.freeze({
  name: 'save_publication_draft',
});
const SAVE_PUBLICATION_DRAFT_TOOL = Object.freeze({
  type: 'function',
  function: {
    name: TOOL_PROBE.name,
    description: 'Save one unpublished publication draft for human review.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        title: {type: 'string', minLength: 1},
        content: {type: 'string', minLength: 1},
      },
      required: ['title', 'content'],
    },
  },
});
const TOOL_CALL_PROMPT = [
  'Craft a concise review-bound Markdown draft from this source fact:',
  JSON.stringify(TRANSLATION_SOURCE_TEXT),
  `Call ${TOOL_PROBE.name} exactly once.`,
  'Supply a nonblank title and complete nonblank Markdown content.',
].join(' ');

function normalizedBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}

function installedModelNames(payload) {
  if (!payload || !Array.isArray(payload.models)) return [];
  return payload.models
    .map((entry) => String((entry && (entry.name || entry.model)) || '').trim())
    .filter(Boolean);
}

function modelIsInstalled(available, required) {
  if (available.includes(required)) return true;
  // Ollama reports an implicit default tag as `:latest`, while clients may
  // lawfully request the same model without spelling that tag.
  return !required.includes(':') && available.includes(`${required}:latest`);
}

function firstEmbedding(payload) {
  if (payload && Array.isArray(payload.data)) {
    const row = payload.data[0];
    return row && Array.isArray(row.embedding) ? row.embedding : [];
  }
  if (payload && Array.isArray(payload.embeddings)) {
    return Array.isArray(payload.embeddings[0]) ? payload.embeddings[0] : [];
  }
  return [];
}

async function jsonBody(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function translationSummary(payload, requiredModel) {
  const objectPayload = Boolean(payload && typeof payload === 'object' && !Array.isArray(payload));
  const model = objectPayload && typeof payload.model === 'string' ? payload.model : '';
  const message = objectPayload && payload.message
    && typeof payload.message === 'object' && !Array.isArray(payload.message)
    ? payload.message
    : null;
  const content = message && typeof message.content === 'string' ? message.content : '';
  let parsedContent = null;
  try {
    parsedContent = JSON.parse(content);
  } catch {
    parsedContent = null;
  }
  const translatedText = parsedContent && typeof parsedContent.translated_text === 'string'
    ? parsedContent.translated_text
    : '';
  const structuredJson = parsedContent !== null;
  const exactShape = Boolean(
    parsedContent
    && typeof parsedContent === 'object'
    && !Array.isArray(parsedContent)
    && Object.keys(parsedContent).length === 1
    && Object.hasOwn(parsedContent, 'translated_text')
    && typeof parsedContent.translated_text === 'string'
  );
  const nonblankTranslatedText = translatedText.trim().length > 0;
  const done = objectPayload && payload.done === true;
  const doneReason = objectPayload && typeof payload.done_reason === 'string'
    ? payload.done_reason
    : '';
  const evalCount = objectPayload && Number.isInteger(payload.eval_count)
    ? payload.eval_count
    : 0;
  const thinkingBlank = !message
    || typeof message.thinking !== 'string'
    || message.thinking.trim().length === 0;
  const errorFree = objectPayload && !Object.hasOwn(payload, 'error');
  const wellFormed = Boolean(
    objectPayload
    && model === requiredModel
    && message
    && message.role === 'assistant'
    && done
    && doneReason === 'stop'
    && evalCount > 0
    && thinkingBlank
    && errorFree
    && structuredJson
    && exactShape
    && nonblankTranslatedText
  );

  return {
    model,
    done,
    doneReason,
    evalCount,
    thinkingBlank,
    errorFree,
    structuredJson,
    exactShape,
    nonblankTranslatedText,
    translatedTextLength: translatedText.length,
    wellFormed,
  };
}

function agentToolCallSummary(payload, requiredModel) {
  const objectPayload = Boolean(payload && typeof payload === 'object' && !Array.isArray(payload));
  const model = objectPayload && typeof payload.model === 'string' ? payload.model : '';
  const choices = objectPayload && Array.isArray(payload.choices) ? payload.choices : [];
  const choice = choices.length === 1 && choices[0]
    && typeof choices[0] === 'object' && !Array.isArray(choices[0])
    ? choices[0]
    : null;
  const message = choice && choice.message
    && typeof choice.message === 'object' && !Array.isArray(choice.message)
    ? choice.message
    : null;
  const calls = message && Array.isArray(message.tool_calls) ? message.tool_calls : [];
  const call = calls.length === 1 && calls[0]
    && typeof calls[0] === 'object' && !Array.isArray(calls[0])
    ? calls[0]
    : null;
  const fn = call && call.function
    && typeof call.function === 'object' && !Array.isArray(call.function)
    ? call.function
    : null;
  const argumentText = fn && typeof fn.arguments === 'string' ? fn.arguments : '';
  let args = null;
  try {
    args = JSON.parse(argumentText);
  } catch {
    args = null;
  }
  const exactArguments = Boolean(
    args
    && typeof args === 'object'
    && !Array.isArray(args)
    && Object.keys(args).length === 2
    && typeof args.title === 'string'
    && args.title.trim().length > 0
    && typeof args.content === 'string'
    && args.content.trim().length > 0
  );
  const thinkingBlank = !message
    || ((typeof message.thinking !== 'string' || message.thinking.trim().length === 0)
      && (typeof message.reasoning !== 'string' || message.reasoning.trim().length === 0));
  const contentBlank = !message
    || message.content === null
    || typeof message.content === 'undefined'
    || (typeof message.content === 'string' && message.content.trim().length === 0);
  const errorFree = objectPayload && !Object.hasOwn(payload, 'error');
  const wellFormed = Boolean(
    objectPayload
    && model === requiredModel
    && choice
    && choice.finish_reason === 'tool_calls'
    && message
    && message.role === 'assistant'
    && contentBlank
    && call
    && call.type === 'function'
    && fn
    && fn.name === TOOL_PROBE.name
    && argumentText
    && exactArguments
    && thinkingBlank
    && errorFree
  );

  return {
    model,
    choiceCount: choices.length,
    finishReason: choice && typeof choice.finish_reason === 'string' ? choice.finish_reason : '',
    toolCallCount: calls.length,
    toolName: fn && typeof fn.name === 'string' ? fn.name : '',
    argumentsJson: args !== null,
    exactArguments,
    contentBlank,
    thinkingBlank,
    errorFree,
    draftContentLength: exactArguments ? args.content.length : 0,
    wellFormed,
  };
}

async function probe(env = process.env, fetchImpl = globalThis.fetch) {
  const embedBaseUrl = normalizedBaseUrl(env.EMBED_PROVIDER_BASE_URL);
  const ollamaBaseUrl = normalizedBaseUrl(env.OLLAMA_BASE_URL);
  const translationModel = String(env.KNOXX_DEPLOY_TRANSLATION_MODEL || '').trim();
  const expectedEmbeddingModel = String(env.KNOXX_DEPLOY_EMBEDDING_MODEL || '').trim();
  const configuredEmbeddingModel = String(env.EMBED_PROVIDER_MODEL || '').trim();
  const expectedDimensions = Number(env.KNOXX_DEPLOY_EMBEDDING_DIMENSIONS);
  const configuredDimensions = Number(env.EMBED_PROVIDER_DIMENSIONS);
  const timeoutMs = Number(env.BACKEND_PROBE_TIMEOUT_MS) || 15000;
  const inferenceTimeoutMs = Number(env.KNOXX_OLLAMA_INFERENCE_TIMEOUT_MS);
  const requiredModels = [...new Set([translationModel, expectedEmbeddingModel].filter(Boolean))];

  const configurationValid = Boolean(
    embedBaseUrl
    && ollamaBaseUrl
    && embedBaseUrl === ollamaBaseUrl
    && translationModel === REQUIRED_TRANSLATION_MODEL
    && expectedEmbeddingModel
    && configuredEmbeddingModel === expectedEmbeddingModel
    && Number.isInteger(expectedDimensions)
    && expectedDimensions > 0
    && Number.isInteger(configuredDimensions)
    && configuredDimensions === expectedDimensions
    && Number.isInteger(inferenceTimeoutMs)
    && inferenceTimeoutMs >= 1
    && inferenceTimeoutMs <= 180000
  );

  if (!configurationValid) {
    return {
      ok: false,
      reason: 'invalid-ollama-configuration',
      routing: {directToOllama: embedBaseUrl !== '' && embedBaseUrl === ollamaBaseUrl},
      models: {
        translation: translationModel,
        expectedEmbedding: expectedEmbeddingModel,
        configuredEmbedding: configuredEmbeddingModel,
      },
      expectedDimensions,
      configuredDimensions,
      inferenceTimeoutMs,
    };
  }

  try {
    const tagsResponse = await fetchImpl(`${ollamaBaseUrl}/api/tags`, {
      signal: AbortSignal.timeout(timeoutMs),
    });
    const tagsPayload = await jsonBody(tagsResponse);
    const available = installedModelNames(tagsPayload);
    const missing = requiredModels.filter((model) => !modelIsInstalled(available, model));

    const translationResponse = await fetchImpl(`${ollamaBaseUrl}/api/chat`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: translationModel,
        messages: [{role: 'user', content: INFERENCE_PROMPT}],
        format: TRANSLATION_SCHEMA,
        stream: false,
        think: false,
        options: {temperature: 0, seed: 0},
      }),
      signal: AbortSignal.timeout(inferenceTimeoutMs),
    });
    const translationPayload = await jsonBody(translationResponse);
    const translation = translationSummary(translationPayload, translationModel);

    // The post drafter uses Ollama's OpenAI-compatible required-tool transport.
    // A native translation completion alone cannot prove this parser path or
    // the exact save_publication_draft arguments are healthy.
    //
    // The body is the deployed request, field for field. The openai-completions
    // adapter emits `reasoning_effort` only when the model declares reasoning
    // AND compat.supportsReasoningEffort; contracts/knoxx/models/gemma4_e2b.edn
    // declares both false, so production omits the field and so does this
    // canary. check-edn-contracts.clj holds that declaration in place, and the
    // thinkingBlank assertion below still proves the turn carried no reasoning.
    const agentToolCallResponse = await fetchImpl(`${ollamaBaseUrl}/v1/chat/completions`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: translationModel,
        messages: [
          {
            role: 'system',
            content: 'You craft one grounded Markdown post and must call the supplied tool.',
          },
          {role: 'user', content: TOOL_CALL_PROMPT},
        ],
        tools: [SAVE_PUBLICATION_DRAFT_TOOL],
        tool_choice: {
          type: 'function',
          function: {name: TOOL_PROBE.name},
        },
        stream: false,
        temperature: 0,
        seed: 0,
      }),
      signal: AbortSignal.timeout(inferenceTimeoutMs),
    });
    const agentToolCallPayload = await jsonBody(agentToolCallResponse);
    const agentToolCall = agentToolCallSummary(agentToolCallPayload, translationModel);

    const embeddingResponse = await fetchImpl(`${embedBaseUrl}/v1/embeddings`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: configuredEmbeddingModel,
        input: ['knoxx deployment embedding probe'],
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const embeddingPayload = await jsonBody(embeddingResponse);
    const vector = firstEmbedding(embeddingPayload);
    const vectorFinite = vector.length > 0
      && vector.every((value) => typeof value === 'number' && Number.isFinite(value));
    const dimensionsMatch = vector.length === expectedDimensions;

    return {
      ok: tagsResponse.status === 200
        && missing.length === 0
        && translationResponse.status === 200
        && translation.wellFormed
        && translation.nonblankTranslatedText
        && agentToolCallResponse.status === 200
        && agentToolCall.wellFormed
        && embeddingResponse.status === 200
        && vectorFinite
        && dimensionsMatch,
      routing: {directToOllama: true},
      catalog: {
        status: tagsResponse.status,
        required: requiredModels,
        missing,
        available,
      },
      translation: {
        status: translationResponse.status,
        timeoutMs: inferenceTimeoutMs,
        ...translation,
      },
      agentToolCall: {
        status: agentToolCallResponse.status,
        timeoutMs: inferenceTimeoutMs,
        ...agentToolCall,
      },
      embedding: {
        status: embeddingResponse.status,
        model: configuredEmbeddingModel,
        expectedDimensions,
        configuredDimensions,
        vectorLength: vector.length,
        nonemptyFiniteVector: vectorFinite,
        dimensionsMatch,
      },
    };
  } catch (error) {
    return {
      ok: false,
      reason: 'ollama-probe-threw',
      detail: String((error && error.message) || error),
    };
  }
}

async function selfTest() {
  const assert = require('node:assert/strict');
  const baseEnv = {
    OLLAMA_BASE_URL: 'http://172.30.114.1:11434/',
    EMBED_PROVIDER_BASE_URL: 'http://172.30.114.1:11434',
    EMBED_PROVIDER_MODEL: 'nomic-embed-text',
    EMBED_PROVIDER_DIMENSIONS: '768',
    KNOXX_DEPLOY_TRANSLATION_MODEL: 'gemma4:e2b',
    KNOXX_DEPLOY_EMBEDDING_MODEL: 'nomic-embed-text',
    KNOXX_DEPLOY_EMBEDDING_DIMENSIONS: '768',
    BACKEND_PROBE_TIMEOUT_MS: '15000',
    KNOXX_OLLAMA_INFERENCE_TIMEOUT_MS: '90000',
  };

  const response = (status, payload) => ({
    status,
    async json() { return payload; },
  });
  const nonJsonResponse = (status) => ({
    status,
    async json() { throw new Error('not json'); },
  });
  const tagsPayload = {
    models: [{name: 'gemma4:e2b'}, {name: 'nomic-embed-text:latest'}],
  };
  const translationPayload = {
    model: 'gemma4:e2b',
    message: {
      role: 'assistant',
      content: JSON.stringify({
        translated_text: 'El documento de despliegue está listo para la revisión humana.',
      }),
    },
    done: true,
    done_reason: 'stop',
    eval_count: 17,
  };
  const agentToolCallPayload = {
    model: 'gemma4:e2b',
    choices: [{
      finish_reason: 'tool_calls',
      message: {
        role: 'assistant',
        content: '',
        tool_calls: [{
          id: 'call_deployment_probe',
          index: 0,
          type: 'function',
          function: {
            name: 'save_publication_draft',
            arguments: JSON.stringify({
              content: '# Deployment Review\n\nThe deployment document is ready for human review.',
              title: 'Deployment Review',
            }),
          },
        }],
      },
    }],
  };
  const embeddingPayload = {
    data: [{embedding: new Array(768).fill(0.25)}],
  };
  const goodPayloadFor = (url) => {
    if (url.endsWith('/api/tags')) return tagsPayload;
    if (url.endsWith('/api/chat')) return translationPayload;
    if (url.endsWith('/v1/chat/completions')) return agentToolCallPayload;
    if (url.endsWith('/v1/embeddings')) return embeddingPayload;
    throw new Error(`unexpected probe URL: ${url}`);
  };
  const calls = [];
  const goodFetch = async (url, options = {}) => {
    calls.push({url, options});
    return response(200, goodPayloadFor(url));
  };

  const good = await probe(baseEnv, goodFetch);
  assert.equal(good.ok, true);
  assert.equal(good.translation.wellFormed, true);
  assert.equal(good.translation.structuredJson, true);
  assert.equal(good.translation.exactShape, true);
  assert.equal(good.translation.nonblankTranslatedText, true);
  assert.equal(good.agentToolCall.wellFormed, true);
  assert.equal(good.agentToolCall.toolName, 'save_publication_draft');
  assert.equal(good.agentToolCall.exactArguments, true);
  assert.equal(good.embedding.vectorLength, 768);
  assert.deepEqual(calls.map((call) => call.url), [
    'http://172.30.114.1:11434/api/tags',
    'http://172.30.114.1:11434/api/chat',
    'http://172.30.114.1:11434/v1/chat/completions',
    'http://172.30.114.1:11434/v1/embeddings',
  ]);
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    model: 'gemma4:e2b',
    messages: [{
      role: 'user',
      content: `Translate the following document text from English to Spanish. Preserve its meaning and return only the requested structured JSON. JSON schema: ${JSON.stringify(TRANSLATION_SCHEMA)} Source text: ${JSON.stringify(TRANSLATION_SOURCE_TEXT)}`,
    }],
    format: TRANSLATION_SCHEMA,
    stream: false,
    think: false,
    options: {temperature: 0, seed: 0},
  });
  assert.equal(calls[1].options.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    model: 'gemma4:e2b',
    messages: [
      {
        role: 'system',
        content: 'You craft one grounded Markdown post and must call the supplied tool.',
      },
      {role: 'user', content: TOOL_CALL_PROMPT},
    ],
    tools: [SAVE_PUBLICATION_DRAFT_TOOL],
    tool_choice: {
      type: 'function',
      function: {name: TOOL_PROBE.name},
    },
    stream: false,
    temperature: 0,
    seed: 0,
  });
  // The deployed openai-completions adapter emits reasoning_effort only for a
  // model contract that declares reasoning; gemma4:e2b declares it off, so the
  // production request carries no such field and neither may this canary.
  const agentRequestBody = JSON.parse(calls[2].options.body);
  assert.equal('reasoning_effort' in agentRequestBody, false);
  assert.equal(calls[2].options.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(calls[3].options.body), {
    model: 'nomic-embed-text',
    input: ['knoxx deployment embedding probe'],
  });
  assert.equal(calls[3].options.headers.Authorization, undefined);

  const shortVector = await probe(baseEnv, async (url) => {
    if (url.endsWith('/v1/embeddings')) {
      return response(200, {data: [{embedding: new Array(767).fill(0.25)}]});
    }
    return response(200, goodPayloadFor(url));
  });
  assert.equal(shortVector.ok, false);
  assert.equal(shortVector.embedding.nonemptyFiniteVector, true);
  assert.equal(shortVector.embedding.dimensionsMatch, false);

  const missingTranslationModel = await probe(baseEnv, async (url) => {
    if (url.endsWith('/api/tags')) {
      return response(200, {models: [{name: 'nomic-embed-text:latest'}]});
    }
    return response(200, goodPayloadFor(url));
  });
  assert.equal(missingTranslationModel.ok, false);
  assert.deepEqual(missingTranslationModel.catalog.missing, ['gemma4:e2b']);

  const withTranslationContent = (value, overrides = {}) => ({
    ...translationPayload,
    ...overrides,
    message: {
      ...translationPayload.message,
      ...(overrides.message || {}),
      content: value,
    },
  });
  const failedTranslationCases = [
    {
      name: 'HTTP failure',
      makeResponse: () => response(503, translationPayload),
      check: (result) => assert.equal(result.translation.status, 503),
    },
    {
      name: 'non-JSON HTTP body',
      makeResponse: () => nonJsonResponse(200),
      check: (result) => assert.equal(result.translation.wellFormed, false),
    },
    {
      name: 'plain model prose',
      makeResponse: () => response(200, withTranslationContent('El documento está listo.')),
      check: (result) => assert.equal(result.translation.structuredJson, false),
    },
    {
      name: 'malformed structured content',
      makeResponse: () => response(200, withTranslationContent('{"translated_text":')),
      check: (result) => assert.equal(result.translation.structuredJson, false),
    },
    {
      name: 'blank translation',
      makeResponse: () => response(200, withTranslationContent(JSON.stringify({translated_text: '   '}))),
      check: (result) => assert.equal(result.translation.nonblankTranslatedText, false),
    },
    {
      name: 'missing translated_text',
      makeResponse: () => response(200, withTranslationContent(JSON.stringify({translation: 'listo'}))),
      check: (result) => assert.equal(result.translation.exactShape, false),
    },
    {
      name: 'non-string translated_text',
      makeResponse: () => response(200, withTranslationContent(JSON.stringify({translated_text: ['listo']}))),
      check: (result) => assert.equal(result.translation.exactShape, false),
    },
    {
      name: 'extra structured field',
      makeResponse: () => response(200, withTranslationContent(JSON.stringify({
        translated_text: 'El documento está listo.',
        review_state: 'approved',
      }))),
      check: (result) => assert.equal(result.translation.exactShape, false),
    },
    {
      name: 'array structured content',
      makeResponse: () => response(200, withTranslationContent(JSON.stringify([
        {translated_text: 'El documento está listo.'},
      ]))),
      check: (result) => assert.equal(result.translation.exactShape, false),
    },
    {
      name: 'unfinished native response',
      makeResponse: () => response(200, {...translationPayload, done: false}),
      check: (result) => assert.equal(result.translation.done, false),
    },
    {
      name: 'length-limited native response',
      makeResponse: () => response(200, {...translationPayload, done_reason: 'length'}),
      check: (result) => assert.equal(result.translation.doneReason, 'length'),
    },
    {
      name: 'wrong response model',
      makeResponse: () => response(200, {...translationPayload, model: 'gemma4:e2b-other'}),
      check: (result) => assert.equal(result.translation.wellFormed, false),
    },
    {
      name: 'missing generation count',
      makeResponse: () => {
        const {eval_count: ignored, ...withoutEvalCount} = translationPayload;
        return response(200, withoutEvalCount);
      },
      check: (result) => assert.equal(result.translation.evalCount, 0),
    },
    {
      name: 'thinking returned despite think false',
      makeResponse: () => response(200, {
        ...translationPayload,
        message: {...translationPayload.message, thinking: 'hidden reasoning'},
      }),
      check: (result) => assert.equal(result.translation.thinkingBlank, false),
    },
    {
      name: 'wrong message role',
      makeResponse: () => response(200, {
        ...translationPayload,
        message: {...translationPayload.message, role: 'tool'},
      }),
      check: (result) => assert.equal(result.translation.wellFormed, false),
    },
    {
      name: 'missing message',
      makeResponse: () => {
        const {message: ignored, ...withoutMessage} = translationPayload;
        return response(200, withoutMessage);
      },
      check: (result) => assert.equal(result.translation.wellFormed, false),
    },
    {
      name: 'embedded Ollama error',
      makeResponse: () => response(200, {...translationPayload, error: 'generation failed'}),
      check: (result) => assert.equal(result.translation.errorFree, false),
    },
  ];
  for (const testCase of failedTranslationCases) {
    const result = await probe(baseEnv, async (url) => (
      url.endsWith('/api/chat')
        ? testCase.makeResponse()
        : response(200, goodPayloadFor(url))
    ));
    assert.equal(result.ok, false, testCase.name);
    testCase.check(result);
  }

  const withToolCall = (toolCallOverrides = {}, messageOverrides = {}, choiceOverrides = {}) => ({
    ...agentToolCallPayload,
    choices: [{
      ...agentToolCallPayload.choices[0],
      ...choiceOverrides,
      message: {
        ...agentToolCallPayload.choices[0].message,
        ...messageOverrides,
        tool_calls: [{
          ...agentToolCallPayload.choices[0].message.tool_calls[0],
          ...toolCallOverrides,
          function: {
            ...agentToolCallPayload.choices[0].message.tool_calls[0].function,
            ...(toolCallOverrides.function || {}),
          },
        }],
      },
    }],
  });
  const failedToolCallCases = [
    {
      name: 'tool-call HTTP failure',
      makeResponse: () => response(503, agentToolCallPayload),
      check: (result) => assert.equal(result.agentToolCall.status, 503),
    },
    {
      name: 'tool-call non-JSON HTTP body',
      makeResponse: () => nonJsonResponse(200),
      check: (result) => assert.equal(result.agentToolCall.wellFormed, false),
    },
    {
      name: 'tool call omitted',
      makeResponse: () => response(200, {
        ...agentToolCallPayload,
        choices: [{finish_reason: 'stop', message: {role: 'assistant', content: 'listo'}}],
      }),
      check: (result) => assert.equal(result.agentToolCall.toolCallCount, 0),
    },
    {
      name: 'wrong required tool',
      makeResponse: () => response(200, withToolCall({function: {name: 'other_tool'}})),
      check: (result) => assert.equal(result.agentToolCall.toolName, 'other_tool'),
    },
    {
      name: 'malformed tool arguments',
      makeResponse: () => response(200, withToolCall({function: {arguments: '{"content":'}})),
      check: (result) => assert.equal(result.agentToolCall.argumentsJson, false),
    },
    {
      name: 'blank draft title',
      makeResponse: () => response(200, withToolCall({
        function: {
          arguments: JSON.stringify({
            content: '# Deployment Review\n\nReady for review.',
            title: '   ',
          }),
        },
      })),
      check: (result) => assert.equal(result.agentToolCall.exactArguments, false),
    },
    {
      name: 'blank draft content',
      makeResponse: () => response(200, withToolCall({
        function: {
          arguments: JSON.stringify({
            content: '   ',
            title: 'Deployment Review',
          }),
        },
      })),
      check: (result) => assert.equal(result.agentToolCall.exactArguments, false),
    },
    {
      name: 'extra tool argument',
      makeResponse: () => response(200, withToolCall({
        function: {
          arguments: JSON.stringify({
            content: '# Deployment Review\n\nReady for review.',
            title: 'Deployment Review',
            publish: true,
          }),
        },
      })),
      check: (result) => assert.equal(result.agentToolCall.exactArguments, false),
    },
    {
      name: 'thinking despite a non-reasoning model contract',
      makeResponse: () => response(200, withToolCall({}, {reasoning: 'hidden reasoning'})),
      check: (result) => assert.equal(result.agentToolCall.thinkingBlank, false),
    },
    {
      name: 'multiple tool calls',
      makeResponse: () => response(200, {
        ...agentToolCallPayload,
        choices: [{
          ...agentToolCallPayload.choices[0],
          message: {
            ...agentToolCallPayload.choices[0].message,
            tool_calls: [
              ...agentToolCallPayload.choices[0].message.tool_calls,
              ...agentToolCallPayload.choices[0].message.tool_calls,
            ],
          },
        }],
      }),
      check: (result) => assert.equal(result.agentToolCall.toolCallCount, 2),
    },
  ];
  for (const testCase of failedToolCallCases) {
    const result = await probe(baseEnv, async (url) => (
      url.endsWith('/v1/chat/completions')
        ? testCase.makeResponse()
        : response(200, goodPayloadFor(url))
    ));
    assert.equal(result.ok, false, testCase.name);
    testCase.check(result);
  }

  const wrongRoute = await probe(
    {...baseEnv, EMBED_PROVIDER_BASE_URL: 'http://proxx:8789'},
    async () => { throw new Error('fetch must not run for invalid routing'); },
  );
  assert.equal(wrongRoute.ok, false);
  assert.equal(wrongRoute.reason, 'invalid-ollama-configuration');
  assert.equal(wrongRoute.routing.directToOllama, false);

  const wrongTranslationModel = await probe(
    {...baseEnv, KNOXX_DEPLOY_TRANSLATION_MODEL: 'gemma4:latest'},
    async () => { throw new Error('fetch must not run for the wrong translation model'); },
  );
  assert.equal(wrongTranslationModel.ok, false);
  assert.equal(wrongTranslationModel.reason, 'invalid-ollama-configuration');

  const wrongConfiguredDimensions = await probe(
    {...baseEnv, EMBED_PROVIDER_DIMENSIONS: '1024'},
    async () => { throw new Error('fetch must not run for mismatched embedding dimensions'); },
  );
  assert.equal(wrongConfiguredDimensions.ok, false);
  assert.equal(wrongConfiguredDimensions.reason, 'invalid-ollama-configuration');
  assert.equal(wrongConfiguredDimensions.expectedDimensions, 768);
  assert.equal(wrongConfiguredDimensions.configuredDimensions, 1024);

  const unboundedInferenceTimeout = await probe(
    {...baseEnv, KNOXX_OLLAMA_INFERENCE_TIMEOUT_MS: '180001'},
    async () => { throw new Error('fetch must not run for an unbounded inference timeout'); },
  );
  assert.equal(unboundedInferenceTimeout.ok, false);
  assert.equal(unboundedInferenceTimeout.reason, 'invalid-ollama-configuration');

  process.stdout.write('probe-ollama: native translation, required tool call, reasoning-off, and 768-vector matrix ok\n');
}

if (process.env.PROBE_SELFTEST === '1') {
  selfTest().catch((error) => {
    process.stderr.write(`${String((error && error.stack) || error)}\n`);
    process.exitCode = 1;
  });
} else {
  probe().then(
    (result) => process.stdout.write(JSON.stringify(result)),
    (error) => process.stdout.write(JSON.stringify({
      ok: false,
      reason: 'probe-threw',
      detail: String((error && error.message) || error),
    })),
  );
}
