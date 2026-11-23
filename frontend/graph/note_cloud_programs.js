// Reuse Sigma's tested circle vertex geometry, buffers and uniforms. Only the
// visible glyph differs; picking keeps the complete disc so hollow targets are
// easy to hover even when the pointer is over their transparent center.
const COMMON = `
precision highp float;
varying vec4 v_color;
varying vec2 v_diffVector;
varying float v_radius;
uniform float u_correctionRatio;
const vec4 transparent = vec4(0.0);
`;

export const UNRESOLVED_FRAGMENT = `${COMMON}
void main(void) {
  float distance = length(v_diffVector);
  #ifdef PICKING_MODE
    gl_FragColor = distance <= v_radius ? v_color : transparent;
  #else
    float feather = u_correctionRatio * 2.0;
    float thickness = max(v_radius * 0.32, feather * 0.8);
    float outer = 1.0 - smoothstep(v_radius - feather, v_radius, distance);
    float inner = smoothstep(max(0.0, v_radius - thickness - feather),
      max(feather * 0.1, v_radius - thickness), distance);
    gl_FragColor = v_color * outer * inner;
  #endif
}
`;

export const EXPERT_FRAGMENT = `${COMMON}
void main(void) {
  float distance = abs(v_diffVector.x) + abs(v_diffVector.y);
  #ifdef PICKING_MODE
    gl_FragColor = length(v_diffVector) <= v_radius ? v_color : transparent;
  #else
    float feather = u_correctionRatio * 2.0;
    float opacity = 1.0 - smoothstep(v_radius - feather, v_radius, distance);
    gl_FragColor = v_color * opacity;
  #endif
}
`;

export function createCloudNodePrograms(CircleProgram) {
  class UnresolvedProgram extends CircleProgram {
    getDefinition() {
      return {
        ...super.getDefinition(),
        FRAGMENT_SHADER_SOURCE: UNRESOLVED_FRAGMENT,
      };
    }
  }
  class ExpertProgram extends CircleProgram {
    getDefinition() {
      return {
        ...super.getDefinition(),
        FRAGMENT_SHADER_SOURCE: EXPERT_FRAGMENT,
      };
    }
  }
  return {
    "cloud-unresolved": UnresolvedProgram,
    "cloud-expert": ExpertProgram,
  };
}
