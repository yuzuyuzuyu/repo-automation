// Apply only the adapter's age controls without replacing repository package rules.
export function configureRenovate(config, policy) {
  if (config.force?.packageRules) {
    throw new Error('force.packageRules discards repository policy; use packageRules');
  }
  return {
    ...config,
    customEnvVariables: {
      ...config.customEnvVariables,
      UV_EXCLUDE_NEWER: 'P3D',
      npm_config_min_release_age: '3',
      npm_config_ignore_scripts: 'true',
    },
    packageRules: [
      ...(config.packageRules || []),
      ...policy.packageRules.map((rule) => ({
        ...rule,
        // This is global runner configuration. Force only these scalar controls
        // on matching dependencies; normal rule merging preserves local policy.
        force: Object.fromEntries(
          ['minimumReleaseAge', 'minimumReleaseAgeBehaviour', 'internalChecksFilter']
            .filter((key) => Object.hasOwn(rule, key)).map((key) => [key, rule[key]]),
        ),
      })),
    ],
  };
}
