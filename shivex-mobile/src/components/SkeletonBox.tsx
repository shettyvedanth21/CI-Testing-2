import { useEffect, useRef } from "react";
import { Animated, StyleSheet, ViewStyle } from "react-native";

import { colors } from "../constants/colors";

type SkeletonBoxProps = {
  width?: ViewStyle["width"];
  height?: number;
  borderRadius?: number;
};

export function SkeletonBox({
  width = "100%",
  height = 16,
  borderRadius = 8,
}: SkeletonBoxProps) {
  const opacity = useRef(new Animated.Value(0.3)).current;

  useEffect(() => {
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 0.7,
          duration: 800,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 0.3,
          duration: 800,
          useNativeDriver: true,
        }),
      ])
    );

    animation.start();
    return () => animation.stop();
  }, [opacity]);

  return (
    <Animated.View
      style={[
        styles.box,
        {
          width,
          height,
          borderRadius,
          opacity,
        },
      ]}
    />
  );
}

const styles = StyleSheet.create({
  box: {
    backgroundColor: colors.border,
  },
});
