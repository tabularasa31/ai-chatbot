import { motion } from 'framer-motion';
import { useScrollAnimation } from '../hooks/useScrollAnimation';

export function Stats() {
  const [ref, isInView] = useScrollAnimation();

  const stats = [
    { value: '47', label: 'sessions' },
    { value: '143', label: 'messages' },
    { value: '12,450', label: 'tokens' },
  ];

  return (
    <section className="max-w-7xl mx-auto px-6 py-20">
      <motion.div
        ref={ref}
        initial={{ opacity: 0, y: 20 }}
        animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
        transition={{ duration: 0.6 }}
        className="flex flex-col md:flex-row items-center justify-center gap-12 md:gap-16"
      >
        {stats.map((stat, index) => (
          <motion.div
            key={index}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={isInView ? { opacity: 1, scale: 1 } : { opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.5, delay: index * 0.1 }}
            className="text-center"
          >
            <div className="text-[#E879F9] text-5xl md:text-6xl mb-2">
              {stat.value}
            </div>
            <div className="text-[#FAF5FF]/60 text-lg">{stat.label}</div>
          </motion.div>
        ))}
      </motion.div>
    </section>
  );
}